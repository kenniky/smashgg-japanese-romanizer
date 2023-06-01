from ultrank_tiering import Tournament, startgg_slug_regex, TournamentTieringResult
import csv
import os 
import re
import sys

true_values = ['true', 't', '1']

def bulk_score(slugs):
    """Scores multiple slugs, and returns the resultant result."""

    # Create results directory
    if not os.path.isdir('tts_values'):
        os.mkdir('tts_values')

    # Get values
    results = []

    for slug_obj in slugs:
        slug = slug_obj['slug']
        invit = slug_obj['invit']

        if startgg_slug_regex.fullmatch(slug):
            print('calculating for slug {}'.format(slug))

            try:
                t = Tournament(slug, invit)
                result = t.calculate_tier()

                results.append(result)

            except Exception as e:
                print(e)
                print('catastrophic failure')
                results.append(slug)
        else:
            print('skipping slug {}'.format(slug))
            results.append(slug)

    return results


def write_results(results, directory='tts_values'):
    # Write CSV

    print('writing summary file')

    if not os.path.isdir(directory):
        os.mkdir(directory)

    with open(os.path.join(directory, 'summary.csv'), newline='', mode='w') as summary_file:
        writer = csv.DictWriter(summary_file, ['Tournament', 'Event', 'Slug', 'URL', 'Invitational?', 'Score', 'Max Potential Score', 'Num Entrants', 'Meets Reqs'])
        writer.writeheader()

        for result in results:
            if isinstance(result, TournamentTieringResult):
                writer.writerow({'Tournament': result.tournament,
                                 'Event': result.event,
                                 'Slug': result.slug,
                                 'URL': 'https://start.gg/' + result.slug,
                                 'Invitational?': str(result.is_invitational),
                                 'Score': result.score,
                                 'Max Potential Score': result.max_potential_score(),
                                 'Num Entrants': result.entrants, 
                                 'Meets Reqs': str(result.should_count())})
            else:
                writer.writerow({'Tournament': '',
                                 'Event': '',
                                 'Slug': str(result),
                                 'URL': '',
                                 'Invitational?': '',
                                 'Score': '',
                                 'Max Potential Score': '',
                                 'Num Entrants': ''})

    for result in results:
        if isinstance(result, TournamentTieringResult):
            print('writing for slug {}'.format(result.slug))

            with open(os.path.join(directory, '{}.txt'.format(re.sub(r'tournament\/([a-z0-9-_]*)\/event\/([a-z0-9-_]*)', r'\1_\2', result.slug))), mode='w') as write_file:
                result.write_result(write_file)

    print('done writing')


if __name__ == '__main__':
    # Get file
    file = input('input file to read keys from: ')

    if not os.path.exists(file):
        print('file doesn\'t exist!')
        sys.exit()

    # Read in values
    slugs = []

    _, ext = os.path.splitext(file)

    if ext == '.csv':
        with open(file, newline='') as file_obj:
            reader = csv.DictReader(file_obj)

            for row in reader:
                slug = row['startgg slug']

                if len(row) > 1:
                    is_invit = row['Is Invitational?'].lower() in true_values
                else:
                    is_invit = False

                slugs.append({'slug': slug, 'invit': is_invit})
    else:
        with open(file) as file_obj:
            for row in file_obj:
                slugs.append({'slug': row.strip(), 'invit': False})

    print('read values')

    results = bulk_score(slugs)
    write_results(results)