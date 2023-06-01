# Requires dateparser, which you can install via `pip install dateparser`.

from startgg_toolkit import send_request
import dateparser
import csv
import os
from Levenshtein import jaro_winkler
from datetime import datetime, timedelta
from ultrank_bulk import bulk_score, write_results

# defines the minimum Jaro-Winkler similarity to
# categorize a tournament as a related iteration.
MINIMUM_JARO_SIMILARITY = 0.8


class Tournament:
    def __init__(self, name, slug, start_at):
        self.name = name
        self.slug = slug
        self.start_at = start_at
        self.time_since = start_at


def tournaments_query(start_time, end_time, page=1, per_page=75):
    query = '''query tournamentsQuery($pageNum: Int!, $perPage: Int!, $startTime: Timestamp!, $endTime: Timestamp!) {
  tournaments (
    query: {
      page: $pageNum,
      perPage: $perPage,
      filter: {
        hasOnlineEvents: false,
        videogameIds: [1386],
        afterDate: $startTime,
        beforeDate: $endTime
      }
    }
  ) {
    pageInfo {
      totalPages
    }
    nodes {
      slug
      name
      events {
        name
        type
        videogame {
          id
        }
        slug
        numEntrants
      }
    }
  }
}'''
    variables = '''{{
        "pageNum": {},
        "perPage": {},
        "startTime": {},
        "endTime": {}
    }}'''.format(page, per_page, start_time, end_time)

    return query, variables


def admin_query(tournament_slug, page=1, per_page=75):
    query = '''query tournamentAdminQuery($tournamentSlug: String!, $pageNum: Int!, $perPage: Int!) {
  tournament(slug: $tournamentSlug) {
    name
    startAt
    owner {
      id
      player {
        gamerTag
      }
      tournaments(
        query: {
          page: $pageNum,
          perPage: $perPage,
          filter: {
            videogameId: [1386]
          }
        }
      ) {
        pageInfo {
          totalPages
        }
        nodes {
          name
          slug
          startAt
          owner {
            id
          }
        }
      }
    }
  }
}'''
    variables = '''{{
        "tournamentSlug": "{}",
        "pageNum": {},
        "perPage": {}
    }}'''.format(tournament_slug, page, per_page)

    return query, variables


def get_admined_tournaments(tournament_slug, day_range=15):
    """Gather all tournament names with the same owner as the requested tournament,
    within the specified day range prior.

    Puts the requested tournament as the first item in the returned array.
    """

    page = 1
    tournaments = []
    tournament_name = None
    tournament_owner_id = None
    tournament_start = None
    range_start = None

    while True:
        query, variables = admin_query(tournament_slug, page)
        resp = send_request(query, variables, quiet=True)

        # Set tournament-specific variables if not set
        if tournament_name == None:
            tournament_name = resp['data']['tournament']['name']
        if tournament_owner_id == None:
            tournament_owner_id = resp['data']['tournament']['owner']['id']
        if tournament_start == None:
            tournament_start = resp['data']['tournament']['startAt']
        if range_start == None:
            tournament_start_datetime = datetime.fromtimestamp(
                tournament_start)
            range_start_timedelta = timedelta(days=day_range)
            range_start = (tournament_start_datetime -
                           range_start_timedelta).timestamp()

        # Gather tournaments
        tournaments.extend([Tournament(tournament['name'], tournament['slug'], tournament['startAt']) for tournament in resp['data']['tournament']['owner']['tournaments']['nodes'] if (
            tournament['owner']['id'] == tournament_owner_id and tournament['slug'] != tournament_slug and tournament['startAt'] >= range_start and tournament['startAt'] <= tournament_start)])

        # Check if all tournaments are before the requested tournament.
        # Since the API returns tournaments in reverse chronological order, this means that we don't need to check the rest.
        if len([tournament for tournament in resp['data']['tournament']['owner']['tournaments']['nodes'] if (tournament['owner']['id'] == resp['data']['tournament']['owner']['id'] and tournament['startAt'] <= tournament_start)]) == 0:
            break

        if page >= resp['data']['tournament']['owner']['tournaments']['pageInfo']['totalPages']:
            break
        page += 1

    tournaments.insert(0, Tournament(
        tournament_name, tournament_slug, tournament_start))

    return tournaments


def check_potential_weekly(tournament_slug):

    other_admined_tournaments = get_admined_tournaments(tournament_slug)

    base_tournament = other_admined_tournaments[0]

    for tournament in other_admined_tournaments[1:]:
        if jaro_winkler(base_tournament.name, tournament.name, score_cutoff=MINIMUM_JARO_SIMILARITY) != 0:
            tournament.time_since = base_tournament.start_at - tournament.start_at
            return tournament

    return None


def retrieve_event_slugs(start_time, end_time, directory='tts_values'):
    page = 1
    slugs = []

    if not os.path.isdir(directory):
        os.mkdir(directory)

    with open(os.path.join(directory, 'events.csv'), newline='', mode='w') as events_file:
        writer = csv.DictWriter(
            events_file, ['Tournament', 'Event', 'Slug', 'Used', 'Skip Reason'])
        writer.writeheader()

        while True:
            query, variables = tournaments_query(
                start_time, end_time, page=page)
            resp = send_request(query, variables, quiet=True)

            print('checking {} tournaments'.format(len(resp['data']['tournaments']['nodes'])))

            for tournament in resp['data']['tournaments']['nodes']:
                try:
                    events = [event for event in tournament['events'] if (
                        event['type'] == 1 and event['videogame']['id'] == 1386 and event['numEntrants'] != None)]

                    events.sort(
                        reverse=True, key=lambda event: event['numEntrants'])

                    added_event = False

                    potential_weekly = "not checked"

                    for event in events:

                        if tournament['name'].lower().find('weekly') != -1 or event['name'].lower().find('weekly') != -1:
                            writer.writerow({'Tournament': tournament['name'],
                                             'Event': event['name'],
                                             'Slug': event['slug'],
                                             'Used': 'False',
                                             'Skip Reason': 'Probable Weekly (contains string "weekly")'})
                            continue

                        if tournament['name'].lower().find('arcadian') != -1 or event['name'].lower().find('arcadian') != -1:
                            writer.writerow({'Tournament': tournament['name'],
                                             'Event': event['name'],
                                             'Slug': event['slug'],
                                             'Used': 'False',
                                             'Skip Reason': 'Probable Arcadian (contains string "arcadian")'})
                            continue

                        if event['name'].lower().find('redemption') != -1:
                            writer.writerow({'Tournament': tournament['name'],
                                             'Event': event['name'],
                                             'Slug': event['slug'],
                                             'Used': 'False',
                                             'Skip Reason': 'Probable Side Event (contains string "redemption")'})
                            continue

                        if event['name'].lower().find('buster') != -1:
                            writer.writerow({'Tournament': tournament['name'],
                                             'Event': event['name'],
                                             'Slug': event['slug'],
                                             'Used': 'False',
                                             'Skip Reason': 'Probable Side Event (contains string "buster")'})
                            continue

                        if event['name'].lower().find('amateur') != -1:
                            writer.writerow({'Tournament': tournament['name'],
                                             'Event': event['name'],
                                             'Slug': event['slug'],
                                             'Used': 'False',
                                             'Skip Reason': 'Probable Side Event (contains string "amateur")'})
                            continue

                        if event['name'].lower().find('squad') != -1:
                            writer.writerow({'Tournament': tournament['name'],
                                             'Event': event['name'],
                                             'Slug': event['slug'],
                                             'Used': 'False',
                                             'Skip Reason': 'Probable Side Event (contains string "squad")'})
                            continue

                        if event['name'].lower().find('random') != -1:
                            writer.writerow({'Tournament': tournament['name'],
                                             'Event': event['name'],
                                             'Slug': event['slug'],
                                             'Used': 'False',
                                             'Skip Reason': 'Probable Side Event (contains string "random")'})
                            continue

                        if event['name'].lower().find('cpu') != -1:
                            writer.writerow({'Tournament': tournament['name'],
                                             'Event': event['name'],
                                             'Slug': event['slug'],
                                             'Used': 'False',
                                             'Skip Reason': 'Probable Side Event (contains string "cpu")'})
                            continue

                        if event['name'].lower().find('amiibo') != -1:
                            writer.writerow({'Tournament': tournament['name'],
                                             'Event': event['name'],
                                             'Slug': event['slug'],
                                             'Used': 'False',
                                             'Skip Reason': 'Probable Side Event (contains string "amiibo")'})
                            continue

                        if event['name'].lower().find('hdr') != -1:
                            writer.writerow({'Tournament': tournament['name'],
                                             'Event': event['name'],
                                             'Slug': event['slug'],
                                             'Used': 'False',
                                             'Skip Reason': 'Probable Side Event (contains string "hdr")'})
                            continue

                        if event['name'].lower().find('wait') != -1:
                            writer.writerow({'Tournament': tournament['name'],
                                             'Event': event['name'],
                                             'Slug': event['slug'],
                                             'Used': 'False',
                                             'Skip Reason': 'Probable Waitlist (contains string "wait")'})
                            continue

                        if added_event:
                            writer.writerow({'Tournament': tournament['name'],
                                             'Event': event['name'],
                                             'Slug': event['slug'],
                                             'Used': 'False',
                                             'Skip Reason': 'Other Larger Event in Tournament'})
                            continue

                        if tournament['name'].lower().find('monthly') != -1 or event['name'].lower().find('monthly') != -1:
                            writer.writerow({'Tournament': tournament['name'],
                                             'Event': event['name'],
                                             'Slug': event['slug'],
                                             'Used': 'True'})

                            slugs.append(event['slug'])
                            added_event = True
                            continue

                        if potential_weekly == "not checked":
                            potential_weekly = check_potential_weekly(tournament['slug'])

                        if isinstance(potential_weekly, Tournament):
                            days_since = str(
                                round(potential_weekly.time_since / (24 * 60 * 60)))

                            writer.writerow({'Tournament': tournament['name'],
                                             'Event': event['name'],
                                             'Slug': event['slug'],
                                             'Used': 'False',
                                             'Skip Reason': 'Probable Weekly (found tournament {} [{}] which precedes by {} days)'.format(potential_weekly.name, potential_weekly.slug, days_since)})
                            added_event = True

                            continue

                        writer.writerow({'Tournament': tournament['name'],
                                         'Event': event['name'],
                                         'Slug': event['slug'],
                                         'Used': 'True'})

                        slugs.append(event['slug'])
                        added_event = True
                except Exception as e:
                    print(e)
                    print(tournament['slug'])

            if page >= resp['data']['tournaments']['pageInfo']['totalPages']:
                break
            page += 1

    return slugs


if __name__ == '__main__':
    start_time_str = input('input starting time for search: ')
    start_time = dateparser.parse(start_time_str)
    start_timestamp = int(start_time.timestamp())

    end_time_str = input('input ending time for search: ')
    end_time = dateparser.parse(end_time_str)
    end_timestamp = int(end_time.timestamp())

    print('using start timestamp {} and end timestamp {}'.format(
        str(start_timestamp), str(end_timestamp)))

    slugs = retrieve_event_slugs(start_timestamp, end_timestamp)

    print('discovered {} tournaments'.format(len(slugs)))
    results = bulk_score([{'slug': slug, 'invit': False} for slug in slugs])
    write_results(results)