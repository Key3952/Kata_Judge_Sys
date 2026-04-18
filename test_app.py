import unittest
import os
import tempfile
import shutil
from csv_manager import CSVManager, CompetitionCSVManager, normalize_protocol_token, sort_prelim_results_for_final_transfer
from scoring import calculate_pair_final_score
from technics import DISCIPLINE_ROWS_BY_KEY

class TestCSVManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.csv_file = os.path.join(self.temp_dir, 'test.csv')
        self.headers = ['name', 'age']
        CSVManager.ensure_csv_exists(self.csv_file, self.headers)

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_write_and_read_csv(self):
        rows = [{'name': 'Alice', 'age': '25'}, {'name': 'Bob', 'age': '30'}]
        CSVManager.write_csv(self.csv_file, rows, self.headers)
        read_rows = CSVManager.read_csv(self.csv_file)
        self.assertEqual(len(read_rows), 2)
        self.assertEqual(read_rows[0]['name'], 'Alice')

    def test_upsert_participant(self):
        headers = CompetitionCSVManager.PARTICIPANTS_HEADERS
        pcsv = os.path.join(self.temp_dir, 'parts.csv')
        CSVManager.ensure_csv_exists(pcsv, headers)
        full = {
            'ФИО': 'Иванов Иван',
            'год рождения': '2000',
            'разряд': '1 дан',
            'кю': '5',
            'СШ': 'СШ1',
            'тренер': 'Петров П.',
        }
        CSVManager.upsert_participant(pcsv, full, headers)
        rows = CSVManager.read_csv(pcsv)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['разряд'], '1 дан')
        full['разряд'] = '2 дан'
        CSVManager.upsert_participant(pcsv, full, headers)
        rows = CSVManager.read_csv(pcsv)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['разряд'], '2 дан')

class TestCompetitionCSVManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.comp_path = os.path.join(self.temp_dir, 'test_comp')
        self.kata_key = 'nagenokata'
        os.makedirs(self.comp_path)
        CompetitionCSVManager.create_discipline_structure(self.comp_path, self.kata_key)

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_create_discipline_structure(self):
        disc_path = os.path.join(self.comp_path, self.kata_key)
        self.assertTrue(os.path.exists(disc_path))
        self.assertTrue(os.path.exists(os.path.join(disc_path, 'protocols')))
        self.assertTrue(os.path.exists(os.path.join(disc_path, 'final_protocol.csv')))
        self.assertTrue(os.path.exists(os.path.join(disc_path, 'participants_list.csv')))
        self.assertTrue(os.path.exists(os.path.join(disc_path, 'final', 'participants_list.csv')))

    def test_read_judge_scores(self):
        disc_path = os.path.join(self.comp_path, self.kata_key)
        protocols_path = os.path.join(disc_path, 'protocols')
        protocol_file = os.path.join(protocols_path, 'Judge1_1_Tori-Uke.csv')
        rows = [{'техника': 'Tech1', 'details_json': '{"m1": 0.5}'}, {'техника': 'Tech2', 'details_json': '{"med": 3.0}'}]
        CSVManager.write_csv(protocol_file, rows, ['техника', 'details_json'])
        
        details = CompetitionCSVManager.read_judge_scores(self.comp_path, self.kata_key, 'Judge1', 1, 'Tori', 'Uke')
        self.assertEqual(details['Tech1']['m1'], 0.5)
        self.assertEqual(details['Tech2']['med'], 3.0)

class TestScoring(unittest.TestCase):
    def test_calculate_pair_final_score(self):
        judge_scores = [85.0, 88.0, 90.0, 87.0, 89.0]  # 5 judges
        final = calculate_pair_final_score(judge_scores)
        # Should be sum of middle 3: 87 + 88 + 89 = 264
        self.assertEqual(final, 264.0)

    def test_calculate_pair_final_score_few_judges(self):
        judge_scores = [85.0, 88.0]  # only 2 judges
        final = calculate_pair_final_score(judge_scores)
        self.assertIsNone(final)

    def test_calculate_pair_final_score_three_judges(self):
        final = calculate_pair_final_score([80.0, 82.5, 84.0], judge_count=3)
        self.assertEqual(final, 246.5)

    def test_calculate_pair_final_score_four_judges(self):
        final = calculate_pair_final_score([80.0, 81.0, 82.0, 83.0], judge_count=4)
        self.assertEqual(final, 326.0)

    def test_calculate_pair_final_score_more_than_five(self):
        final = calculate_pair_final_score([85.0, 88.0, 90.0, 87.0, 89.0, 100.0], judge_count=6)
        self.assertEqual(final, 264.0)


class TestPrelimFinalTransfer(unittest.TestCase):
    def test_sort_by_place_then_sum(self):
        rows = [
            {'номер пары': '3', 'Сумма': '100', 'Место': '2'},
            {'номер пары': '1', 'Сумма': '200', 'Место': '1'},
            {'номер пары': '2', 'Сумма': '300', 'Место': '3'},
        ]
        out = sort_prelim_results_for_final_transfer(rows)
        self.assertEqual([r['номер пары'] for r in out], ['1', '3', '2'])

    def test_fallback_sum_when_no_place(self):
        rows = [
            {'номер пары': '1', 'Сумма': '50', 'Место': ''},
            {'номер пары': '2', 'Сумма': '90', 'Место': ''},
        ]
        out = sort_prelim_results_for_final_transfer(rows)
        self.assertEqual([r['номер пары'] for r in out], ['2', '1'])


class TestProtocolFilenameParsing(unittest.TestCase):
    def test_parse_protocol_stem(self):
        p = CompetitionCSVManager.parse_protocol_filename('John_Doe_1_Ivan_Petrov-Petr_Sidorov')
        self.assertIsNotNone(p)
        jn, jp, slug = p
        self.assertEqual(jp, '1')
        self.assertEqual(jn, 'John_Doe')
        self.assertIn('Ivan', slug)
        self.assertIn('Petrov', slug)

    def test_normalize_tokens_match(self):
        self.assertEqual(
            normalize_protocol_token('Иван Иванов'),
            normalize_protocol_token('Иван_Иванов'),
        )


if __name__ == '__main__':
    unittest.main()
