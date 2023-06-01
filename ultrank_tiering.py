"""Script to generate UltRank tiers.

Requirements:
 geopy installed: pip install geopy
 start.gg API key stored in a file 'smashgg.key'
 From the UltRank TTS Scraping Sheet:
  ultrank_players.csv
  ultrank_regions.csv
  ultrank_invitational.csv
"""

from startgg_toolkit import send_request, startgg_slug_regex
from geopy.geocoders import Nominatim
import csv
import re
import sys
import json
import datetime

NUM_PLAYERS_FLOOR = 2


class InvalidEventUrlException(Exception):
    pass


class PotentialMatchWithDqs:
    def __init__(self, tag, id_, points, note, actual_tag='', dqs=0):
        self.tag = tag
        self.id_ = id_
        self.points = points
        self.note = note
        self.actual_tag = actual_tag if actual_tag != '' else self.tag
        self.dqs = dqs

    def __str__(self):
        actual_tag_portion = '' if self.actual_tag == self.tag else self.actual_tag + ': '
        dq_portion = '' if self.dqs == 0 else ' - {} DQ{}'.format(
            self.dqs, 's' if self.dqs == 1 else '')
        return '{} (id {}) - {}{} points [{}]{}'.format(self.tag, self.id_, actual_tag_portion, self.points, self.note, dq_portion)


class DisqualificationValue:
    """Stores a player value with DQ count."""

    def __init__(self, value, dqs):
        self.value = value
        self.dqs = dqs

    def __str__(self):
        return '{} - {} DQ{}'.format(str(self.value), str(self.dqs), '' if self.dqs == 1 else 's')


class CountedValue:
    """Stores a counted player value with additional data."""

    def __init__(self, player_value, total_points, alt_tag):
        self.player_value = player_value
        self.points = total_points
        self.alt_tag = alt_tag
        self.tag = player_value.tag
        self.id_ = player_value.id_

    def __str__(self):
        full_tag = self.alt_tag + \
            (' (aka {})'.format(self.player_value.tag)
             if self.alt_tag != self.player_value.tag else '')

        return '{} - {} points [{}]'.format(full_tag, self.points, self.player_value.note)


class PlayerValue:
    """Stores scores for players."""

    def __init__(self, id_, tag, points=0, note='', invitational=0, start_time=None, end_time=None):
        self.tag = tag
        self.id_ = id_
        self.points = points
        self.note = note
        self.invitational_val = invitational
        self.start_time = start_time
        self.end_time = end_time

    def __str__(self):
        return '{} (id {}) - {} (+{}) points [{}]'.format(self.tag, self.id_, self.points, self.invitational_val, self.note)

    def is_within_timeframe(self, time):
        if self.start_time != None and time < self.start_time:
            return False
        if self.end_time != None and time >= self.end_time:
            return False

        return True


class PlayerValueGroup:
    """Stores multiple scores for players."""

    def __init__(self, id_, tag, invitational_val=0, other_tags=[]):
        self.tag = tag
        self.id_ = id_
        self.invitational_val = invitational_val
        self.values = []
        self.other_tags = [tag_.lower() for tag_ in other_tags]

    def set_invitational_val(self, value):
        self.invitational_val = value

        for val in self.values:
            val.invitational_val = value

    def add_value(self, points, note='', start_time=None, end_time=None):
        self.values.append(PlayerValue(
            self.id_, self.tag, points, note, self.invitational_val, start_time, end_time))

        self.values.sort(reverse=True, key=lambda val: val.points)

    def retrieve_value(self, tournament):
        for value in self.values:
            if value.is_within_timeframe(tournament.start_time):
                return value
        return None

    def match_tag(self, tag):
        return tag.lower() == self.tag.lower() or tag.lower() in self.other_tags


class TournamentTieringResult:
    def __init__(self, slug, score, entrants, region, values, dqs, potential, is_invitational=False, phases=[], dq_count=-1):
        self.slug = slug
        self.score = score
        self.values = values
        self.dqs = dqs
        self.potential = potential
        self.entrants = entrants
        self.region = region
        self.is_invitational = is_invitational
        self.dq_count = dq_count
        self.phases = phases
        self.max_score = None

        name = get_name(slug)
        self.tournament = name['tournament']
        self.event = name['event']

    def write_result(self, filelike=None):
        original_stdout = sys.stdout

        if filelike != None:
            sys.stdout = filelike

        print('{} - {} ({}){}'.format(self.tournament, self.event,
                                      self.slug, ' (invitational)' if self.is_invitational else ''))
        print('Phases used: {}'.format(str(self.phases)))
        print()

        if not self.should_count():
            print('WARNING: This tournament does not meet the criteria of at least {} entrants or a score of at least {} with {} qualified players'.format(
                self.region.entrant_floor, self.region.score_floor, NUM_PLAYERS_FLOOR))
            print()
        elif not self.should_count_strict():
            print('WARNING: This tournament may not meet the criteria of at least {} entrants or a score of at least {} with {} qualified players'.format(
                self.region.entrant_floor, self.region.score_floor, NUM_PLAYERS_FLOOR))
            print()

        participants_string = '{} - {} DQs = {}'.format(
            self.entrants + self.dq_count, self.dq_count, self.entrants) if self.dq_count != -1 else str(self.entrants)

        print('Entrants: {} x {} [{}] = {}'.format(
            participants_string, self.region.multiplier, self.region.note, self.entrants * self.region.multiplier))

        print()
        print('Top Player Points: ')

        for participant in self.values:
            print('  {}'.format(str(participant)))

        print()
        print('Total Score: {}'.format(self.score))

        if len(self.dqs) > 0:
            print()
            print('-----')
            print('DQs')
            for dq in self.dqs:
                print('  {}'.format(str(dq)))

        if len(self.potential) > 0:
            print()
            print('-----')
            print('Potentially Mismatched Players')
            for match in self.potential:
                print('  {}'.format(str(match)))

        sys.stdout = original_stdout

    def max_potential_score(self):
        if self.max_score != None:
            return self.max_score

        potential_score = self.score

        potential_player_scores = {}

        for pot in self.potential:
            if isinstance(pot, DisqualificationValue):
                potential_player_scores[pot.value.id_] = max(
                    pot.value.points, potential_player_scores.get(pot.value.id_, 0))
            else:
                potential_player_scores[pot.id_] = max(
                    pot.points, potential_player_scores.get(pot.id_, 0))

        dq_scores = {}

        for dq in self.dqs:
            if isinstance(dq.value, CountedValue):
                dq_scores[dq.value.player_value.id_] = dq.value.points
            else:
                dq_scores[dq.value.id_] = max(
                    dq.value.points, dq_scores.get(dq.value.id_, 0))

        for value in potential_player_scores.values():
            potential_score += value

        for value in dq_scores.values():
            potential_score += value

        self.max_score = potential_score

        return potential_score

    def should_count_strict(self):
        return self.entrants >= self.region.entrant_floor or (self.score >= self.region.score_floor and len(self.values) >= NUM_PLAYERS_FLOOR)

    def should_count(self):
        return self.entrants >= self.region.entrant_floor or (self.max_potential_score() >= self.region.score_floor and len(self.values) + len(self.potential) + len(self.dqs) >= NUM_PLAYERS_FLOOR)


class RegionValue:
    """Stores region multipliers."""

    def __init__(self, country_code='', iso2='', county='', city='', jp_postal='', multiplier=1, entrant_floor=64, score_floor=250, note=''):
        self.country_code = country_code
        self.iso2 = iso2
        self.county = county
        self.city = city
        self.jp_postal = jp_postal
        self.multiplier = multiplier
        self.entrant_floor = entrant_floor
        self.score_floor = score_floor
        self.note = note

    def match(self, address):
        """Compares an address derived from Nominatim module to the stored 
        region.
        Higher number = larger match.
        """

        if self.country_code == '':
            return 1

        match = 0

        if address.get('country_code', '') == self.country_code:
            match += 2

            if self.iso2 == '':
                match += 1
            elif address.get('ISO3166-2-lvl4', '') == self.iso2 or address.get('ISO3166-2-lvl3', '') == self.iso2:
                match += 2

                if self.county == '' and self.city == '':
                    match += 1
                elif self.county != '' and address.get('county', '') == self.county:
                    match += 2
                elif self.city != '' and address.get('city', '') == self.city:
                    match += 2

            if self.country_code == 'jp':
                jp_postal = address.get('postcode', 'XX')[0:2]

                if self.jp_postal == '':
                    match += 1
                elif jp_postal == self.jp_postal:
                    match += 2

        return match

    def __eq__(self, other):
        if not isinstance(other, RegionValue):
            return False

        return self.country_code == other.country_code and self.iso2 == other.iso2 and self.county == other.county and self.city == other.city and self.jp_postal == other.jp_postal and self.multiplier == other.multiplier

    def __hash__(self):
        return hash((self.country_code, self.iso2, self.county, self.city, self.jp_postal, self.multiplier))

    def __str__(self):
        ret = ''
        if self.country_code != '':
            ret += '{}'.format(self.country_code)

            if self.iso2 != '':
                ret += '/{}'.format(self.iso2)

                if self.county != '':
                    ret += '/{}'.format(self.county)
                elif self.city != '':
                    ret += '/{}'.format(self.city)

            if self.jp_postal != '':
                ret += '/JP Postal {}'.format(self.jp_postal)

        else:
            ret = 'All Other Regions'
        ret += ' [{}] - x{}'.format(self.note, self.multiplier)

        return ret


class Entrant:
    """Wrapper class to store player ids and tags."""

    def __init__(self, id_num, tag):
        self.id_ = id_num
        self.tag = tag

    def __eq__(self, other):
        if not isinstance(other, Entrant):
            return False
        return self.id_ == other.id_ and self.tag == other.tag

    def __hash__(self):
        return hash((self.id_, self.tag))


class Tournament:
    """Stores tournament info/metadata."""

    def __init__(self, event_slug, is_invitational=False, location=True):
        """Populates tournament metadata with tournament slug/invitational status."""

        match = startgg_slug_regex.search(event_slug)

        if not match:
            raise InvalidEventUrlException

        self.event_slug = match.group(0)
        self.is_invitational = is_invitational
        self.tier = None

        self.gather_entrant_counts()
        if location:
            self.gather_location_info()
        else:
            self.address = {'country_code': 'us'}
        self.retrieve_start_time()

    def gather_entrant_counts(self):
        # Check if the event has progressed enough to detect DQs.
        self.total_dqs = -1  # Placeholder value

        event_progressed = check_phase_completed(self.event_slug)

        if event_progressed:
            self.phases = collect_phases(self.event_slug)

            self.dq_list, self.participants = get_dqs(
                self.event_slug, phase_ids=[phase['id'] for phase in self.phases])

            self.total_dqs = 0

            participant_ids = [part.id_ for part in self.participants]

            for player_id, _ in self.dq_list.items():
                if player_id not in participant_ids:
                    self.total_dqs += 1

            self.total_entrants = len(self.participants) + self.total_dqs

        else:
            self.participants = get_entrants(self.event_slug)
            self.dq_list = {}
            self.total_dqs = -1
            self.total_entrants = len(self.participants)
            self.phases = []

        # Comment out if subtracting generic entrant dqs
        self.total_dqs = -1

    def gather_location_info(self):
        geo = Nominatim(user_agent='ultrank', timeout=10)

        query, variables = location_query(self.event_slug)
        resp = send_request(query, variables)

        try:
            self.lat = resp['data']['event']['tournament']['lat']
            self.lng = resp['data']['event']['tournament']['lng']
        except Exception as e:
            print(e)
            print(resp)
            raise e

        # Try 10 times
        for i in range(5):
            try:
                self.address = geo.reverse('{}, {}'.format(
                    self.lat, self.lng)).raw['address']
                break
            except Exception:
                print(f'Nominatim error {i}')
                pass

    def retrieve_start_time(self):
        query, variables = time_query(self.event_slug)
        resp = send_request(query, variables)

        try:
            self.start_time = datetime.date.fromtimestamp(
                resp['data']['event']['startAt'])
        except Exception as e:
            print(e)
            print(resp)
            raise e

    def calculate_tier(self):
        """Calculates point value of event."""

        if self.tier != None:
            return self.tier

        # add things up
        total_score = 0

        # Entrant score
        best_match = 0
        best_region = None

        for region in region_mults:
            match = region.match(self.address)
            if match > best_match:
                best_region = region
                best_match = match

        total_score += self.total_entrants * best_region.multiplier

        # Player values
        valued_participants = []
        potential_matches = []

        for participant in self.participants:
            if participant.id_ in self.dq_list:
                # Only count fully participating players towards points

                continue
            if participant.id_ in scored_players:
                player_value = scored_players[participant.id_].retrieve_value(
                    self)

                if player_value != None:
                    score = player_value.points + \
                        (player_value.invitational_val if self.is_invitational else 0)

                    total_score += score

                    valued_participants.append(CountedValue(
                        player_value, score, participant.tag))
            elif participant.tag.lower() in scored_tags:
                for player_value_group in scored_players.values():
                    if player_value_group.match_tag(participant.tag):
                        player_value = player_value_group.retrieve_value(self)

                        if player_value != None:
                            score = player_value.points + \
                                (player_value.invitational_val if self.is_invitational else 0)
                            potential_matches.append(PotentialMatchWithDqs(
                                participant.tag, participant.id_, score, player_value.note, player_value.tag))

        # Loop through players with DQs
        participants_with_dqs = []

        for participant, num_dqs in self.dq_list.values():
            if participant.id_ in scored_players:
                player_value = scored_players[participant.id_].retrieve_value(
                    self)

                if player_value != None:
                    score = player_value.points + \
                        (player_value.invitational_val if self.is_invitational else 0)

                    participants_with_dqs.append(DisqualificationValue(
                        CountedValue(player_value, score, participant.tag), num_dqs))
            elif participant.tag.lower() in scored_tags:
                for player_value_group in scored_players.values():
                    if player_value_group.match_tag(participant.tag):
                        player_value = player_value_group.retrieve_value(self)

                        if player_value != None:
                            score = player_value.points + \
                                (player_value.invitational_val if self.is_invitational else 0)
                            potential_matches.append(PotentialMatchWithDqs(
                                participant.tag, participant.id_, score, player_value.note, player_value.tag, num_dqs))

        # Sort for readability
        valued_participants.sort(reverse=True, key=lambda p: p.points)
        participants_with_dqs.sort(
            reverse=True, key=lambda p: (p.dqs, p.value.points))
        potential_matches.sort(key=lambda m: (m.dqs, m.tag))

        self.tier = TournamentTieringResult(self.event_slug, total_score, self.total_entrants, best_region, valued_participants,
                                            participants_with_dqs, potential_matches, is_invitational=self.is_invitational,
                                            phases=[phase['name'] for phase in self.phases], dq_count=self.total_dqs)

        return self.tier


def entrants_query(event_slug, page_num=1, per_page=200):
    query = '''query getEntrants($eventSlug: String!, $pageNum: Int!, $perPage: Int!) {
        event(slug: $eventSlug) {
            entrants(
                query: {
                    page: $pageNum,
                    perPage: $perPage
                }
            ){
                pageInfo {
                    totalPages
                }
                nodes {
                    participants {
                        player {
                            gamerTag
                            id
                        }
                    }
                }
            }
        }
    }'''
    variables = '''{{
        "eventSlug": "{}",
        "pageNum": {},
        "perPage": {}
    }}'''.format(event_slug, page_num, per_page)
    return query, variables


def sets_query(event_slug, page_num=1, per_page=50, phases=None):
    """Generates a query to retrieve sets from an event."""

    query = '''query getSets($eventSlug: String!, $pageNum: Int!, $perPage: Int!, $phases: [ID]!) {
  event(slug: $eventSlug) {
    sets(page: $pageNum, perPage: $perPage, filters:{ state: [3], phaseIds: $phases}) {
      pageInfo {
        page
        totalPages
      }
      nodes {
        wPlacement
        winnerId
        slots {
          entrant {
            id
            participants {
              player {
                gamerTag
                id
              }
            }
          }
          standing {
            stats {
              score {
                value
              }
            }
          }
        }
      }
    }
  }
}'''
    variables = '''{{
        "eventSlug": "{}",
        "pageNum": {},
        "perPage": {},
        "phases": {}
    }}'''.format(event_slug, page_num, per_page, f'{phases if phases is not None else "[]"}')
    return query, variables


def phase_list_query(event_slug):
    """Generates a query to retrieve a list of phases from an event."""

    query = '''query getPhases($eventSlug: String!) {
  event(slug: $eventSlug) {
    phases {
      id
      name
      state
      isExhibition
    }
  }
}'''
    variables = '''{{
        "eventSlug": "{}"
    }}'''.format(event_slug)

    return query, variables


def location_query(event_slug):
    """Generates a query to retrieve the location (latitude/longitude)
    of an event.
    """

    query = '''query getLoc($eventSlug: String!) {
  event(slug: $eventSlug) {
    tournament {
      lat
      lng
    }
  }
}'''
    variables = '''{{
        "eventSlug": "{}"
    }}'''.format(event_slug)

    return query, variables


def time_query(event_slug):
    """Generates a query to retrieve the start time of an event.
    """

    query = '''query getLoc($eventSlug: String!) {
  event(slug: $eventSlug) {
    startAt
  }
}'''
    variables = '''{{
        "eventSlug": "{}"
    }}'''.format(event_slug)

    return query, variables


def name_query(event_slug):
    """Generates a query to retrieve tournament and event name given a slug."""

    query = '''query nameQuery($eventSlug: String!) {
  event(slug: $eventSlug) {
    name
    tournament {
      name
    }
  }
}'''
    variables = '''{{
        "eventSlug": "{}"
    }}'''.format(event_slug)

    return query, variables


def get_sets_in_phases(event_slug, phase_ids):
    """Collects all the sets in a group of phases."""

    page = 1

    sets = []

    while True:
        query, variables = sets_query(
            event_slug, page_num=page, phases=phase_ids)
        resp = send_request(query, variables)

        try:
            sets.extend(resp['data']['event']['sets']['nodes'])
        except Exception as e:
            print(e)
            print(resp)
            raise e

        if page >= resp['data']['event']['sets']['pageInfo']['totalPages']:
            break
        page += 1

    return sets


def check_phase_completed(event_slug):
    """Checks to see if any phases are completed."""

    # Get ordered list of phases
    query, variables = phase_list_query(event_slug)
    resp = send_request(query, variables)

    try:
        for phase in resp['data']['event']['phases']:
            if phase.get('state', '') == 'COMPLETED' and not phase.get('isExhibition', True):
                return True
    except Exception as e:
        print(e)
        print(resp)
        raise e

    return False


def collect_phases(event_slug):
    """Collects phases that are part of the main tournament.
    (Hopefully) excludes amateur brackets.
    """

    # Get ordered list of phases
    query, variables = phase_list_query(event_slug)
    resp = send_request(query, variables)

    return [phase for phase in resp['data']['event']['phases'] if not phase['isExhibition']]


def get_entrants(event_slug):
    page = 1
    participants = set()

    while True:
        query, variables = entrants_query(event_slug, page_num=page)
        resp = send_request(query, variables)

        for entrant in resp['data']['event']['entrants']['nodes']:
            try:
                player_data = Entrant(
                    entrant['participants'][0]['player']['id'], entrant['participants'][0]['player']['gamerTag'])

                participants.add(player_data)
            except Exception as e:
                print(e)
                print(resp)
                raise e

        if page >= resp['data']['event']['entrants']['pageInfo']['totalPages']:
            break
        page += 1

    return participants


def get_dqs(event_slug, phase_ids=None):
    """Retrieves DQs of an event."""

    dq_list = {}
    participants = set()

    for set_data in get_sets_in_phases(event_slug, phase_ids):
        if set_data['winnerId'] == None:
            continue

        loser = 1 if set_data['winnerId'] == set_data['slots'][0]['entrant']['id'] else 0

        player_data_0 = Entrant(set_data['slots'][0]['entrant']['participants'][0]['player']
                                ['id'], set_data['slots'][0]['entrant']['participants'][0]['player']['gamerTag'])
        player_data_1 = Entrant(set_data['slots'][1]['entrant']['participants'][0]['player']
                                ['id'], set_data['slots'][1]['entrant']['participants'][0]['player']['gamerTag'])
        player_data_loser = player_data_0 if loser == 0 else player_data_1

        if set_data['slots'][0]['standing'] == None and set_data['slots'][1]['standing'] == None:
            player_id = set_data['slots'][loser]['entrant']['participants'][0]['player']['id']

            if player_id in dq_list.keys():
                dq_list[player_id][1] += 1
            else:
                dq_list[player_id] = [player_data_loser, 1]
            continue

        game_count = set_data['slots'][loser]['standing']['stats']['score']['value']

        if game_count == -1:
            player_id = set_data['slots'][loser]['entrant']['participants'][0]['player']['id']

            if player_id in dq_list.keys():
                dq_list[player_id][1] += 1
            else:
                dq_list[player_id] = [player_data_loser, 1]
        else:
            # not a dq, record both players as participants
            participants.add(player_data_0)
            participants.add(player_data_1)

    return dq_list, participants


def get_name(event_slug):
    query, variables = name_query(event_slug)
    resp = send_request(query, variables)

    return {'event': resp['data']['event']['name'], 'tournament': resp['data']['event']['tournament']['name']}


def read_players():
    players = {}
    tags = set()
    alt_tags = {}

    try:
        with open('ultrank_tags.csv', newline='', encoding='utf-8') as tags_file:
            reader = csv.reader(tags_file)

            for row in reader:
                alt_tag_list = []
                for tag in row[1:]:
                    if tag != '':
                        alt_tag_list.append(tag)

                if len(alt_tag_list) != 0:
                    alt_tags[row[0]] = alt_tag_list

                    for tag in alt_tag_list:
                        tags.add(tag.lower())

    except FileNotFoundError:
        pass

    with open('ultrank_players.csv', newline='', encoding='utf-8') as players_file:
        reader = csv.DictReader(players_file)

        for row in reader:
            id_ = row['Start.gg Num ID']
            if id_ == '':
                id_ = row['Player']
            else:
                id_ = int(id_)

            tag = row['Player'].strip()
            if tag == '':
                continue

            points = int(row['Points'])

            start_date = datetime.date.fromisoformat(
                row['Start Date']) if row['Start Date'] != '' else None
            end_date = datetime.date.fromisoformat(
                row['End Date']) if row['End Date'] != '' else None

            if id_ not in players:
                player_value_group = PlayerValueGroup(
                    id_, tag, other_tags=alt_tags.get(row['Player'], []))
                players[id_] = player_value_group

            players[id_].add_value(points, row['Note'], start_date, end_date)

            tags.add(tag.lower())

    with open('ultrank_invitational.csv', newline='', encoding='utf-8') as invit_file:
        reader = csv.DictReader(invit_file)

        for row in reader:
            id_ = row['Num']
            if id_ == '':
                id_ = row['Player']
            else:
                id_ = int(id_)

            if id_ in players:
                player_value = players[id_]
                player_value.set_invitational_val(
                    int(row['Additional Points']))

    return players, tags


def read_regions():
    regions = set()

    with open('ultrank_regions.csv', newline='') as regions_file:
        reader = csv.DictReader(regions_file)

        for row in reader:
            region_value = RegionValue(country_code=row['country_code'], iso2=row['ISO3166-2'], county=row['county'],
                                       city = row['city'], jp_postal=row['jp-postal-code'], multiplier=int(
                                           row['Multiplier']),
                                       entrant_floor=int(row['Entrant Floor']), score_floor=int(row['Score Floor']), note=row['Note'])
            regions.add(region_value)

    return regions


scored_players, scored_tags = read_players()
region_mults = read_regions()

if __name__ == '__main__':
    event_slug = input('input event url: ')

    is_invitational = input('is this an invitational? (y/n) ')
    is_invitational = is_invitational.lower() == 'y' or is_invitational.lower() == 'yes'

    tournament = Tournament(event_slug, is_invitational)

    result = tournament.calculate_tier()
    result.write_result()

    print()
    print('Maximum potential total: {}'.format(
        int(result.max_potential_score())))
