"""Check user intent routing and preservation of editable project briefs."""
import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('shadow_profiles', ROOT / 'shadow/skills/shadow/scripts/profiles.py')
profiles = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profiles)


class ProfileTests(unittest.TestCase):
    def test_explicit_profile_respects_choice(self):
        result = profiles.select('用 education-course 做课程招生宣传片，保留我指定的领域')
        self.assertEqual(result['candidates'][0]['id'], 'education-course')
        self.assertTrue(result['requires_material_review'])

    def test_course_is_different_from_course_promotion(self):
        course = profiles.select('制作系统网络课程，按知识点分章，讲稿和课件同步')
        promo = profiles.select('制作课程招生广告，招生宣传，面向潜在学员展示课程价值')
        self.assertEqual(course['candidates'][0]['id'], 'education-course')
        self.assertNotEqual(promo['candidates'][0]['id'], 'education-course')

    def test_deadline_and_full_speech_are_different(self):
        quick = profiles.select('活动当天快剪，当晚发布精彩瞬间')
        full = profiles.select('完整会议演讲，保留完整讲话和问答，不要快剪')
        self.assertEqual(quick['candidates'][0]['id'], 'event-same-day')
        self.assertEqual(full['candidates'][0]['id'], 'conference-talk')

    def test_corporate_purpose(self):
        result = profiles.select('企业宣传片，介绍企业业务、团队和生产流程')
        self.assertEqual(result['candidates'][0]['id'], 'promo-corporate')

    def test_product_launch_scenario_selects_product_purpose(self):
        query = '45秒产品上市预告，受众第一次认识品牌，情绪起伏，不能只是整齐卡点'
        result = profiles.select(query)
        self.assertEqual(result['candidates'][0]['id'], 'promo-product')
        self.assertFalse(result['ambiguous'])
        self.assertIsNone(result['fallback_recipe'])
        self.assertEqual(profiles.select('为新用户制作新品预告')['candidates'][0]['id'], 'promo-product')

    def test_practical_course_scenario_preserves_teaching_purpose(self):
        query = '8分钟焊接操作课程，所有关键安全步骤和参数必须保留，用户给了60fps慢动作和炫酷预告片参考'
        result = profiles.select(query)
        self.assertEqual(result['candidates'][0]['id'], 'education-course')
        self.assertFalse(result['ambiguous'])
        self.assertIsNone(result['fallback_recipe'])
        self.assertEqual(profiles.select('制作焊接实操课程，保留步骤与参数')['candidates'][0]['id'], 'education-course')

    def test_launch_and_practical_course_negation_does_not_add_candidates(self):
        cases = [
            ('不要45秒产品上市预告，而是完整会议记录，保留发言和问答', 'promo-product', 'conference-talk'),
            ('不做新品预告，而是完整会议记录', 'promo-product', 'conference-talk'),
            ('不是8分钟焊接操作课程，而是课程招生广告，面向潜在学员展示课程价值', 'education-course', 'promo-service'),
            ('不需要实操课程，而是课程招生广告', 'education-course', 'promo-service'),
        ]
        for query, excluded, expected in cases:
            with self.subTest(query=query):
                result = profiles.select(query)
                self.assertEqual(result['candidates'][0]['id'], expected)
                self.assertNotIn(excluded, [c['id'] for c in result['candidates']])

    def test_practical_course_name_does_not_override_recruitment_ad(self):
        for query in ['制作焊接操作课程招生广告，面向潜在学员展示课程价值',
                      '制作焊接实操课程招生宣传，介绍收益和报名方式']:
            with self.subTest(query=query):
                result = profiles.select(query)
                self.assertEqual(result['candidates'][0]['id'], 'promo-service')

    def test_no_match_is_explicitly_uncertain(self):
        result = profiles.select('整理一下这些文件')
        self.assertEqual(result['candidates'], [])
        self.assertTrue(result['ambiguous'])
        self.assertEqual(result['fallback_recipe'], 'general-story')

    def test_explicit_negation_is_not_a_positive_tag(self):
        self.assertFalse(profiles.tag_matches('不要快剪', '快剪'))
        self.assertFalse(profiles.tag_matches('not tutorial', 'tutorial'))
        self.assertFalse(profiles.tag_matches('mvp', 'mv'))

    def test_negative_clause_modifiers_do_not_reverse_intent(self):
        query = '我需要完整会议录像。90分钟全程按议程切换机位，讲话提问回答不能删。不要只做60秒活动精彩回顾。'
        result = profiles.select(query)
        self.assertEqual(result['candidates'][0]['id'], 'conference-talk')
        self.assertNotIn('event-recap', [c['id'] for c in result['candidates']])
        self.assertFalse(profiles.tag_matches('不做课程招生广告', '招生广告'))
        self.assertTrue(profiles.tag_matches('不要活动回顾，而是完整会议记录', '完整会议记录'))
        self.assertFalse(profiles.select('不要 event-recap')['candidates'])

    def test_generic_promotion_keeps_candidates(self):
        result = profiles.select('制作一条两分钟宣传片')
        self.assertTrue(result['ambiguous'])
        self.assertGreaterEqual(len(result['candidates']), 2)
        self.assertTrue(all(c['family'] == 'promotion' for c in result['candidates']))

    def args(self, output, **changes):
        values = dict(output=str(output), title='测试片名', audience='新手学员',
                      duration=90.0, aspect='2.35:1', fps=25.0)
        values.update(changes)
        return argparse.Namespace(**values)

    def test_brief_preserves_user_specs_and_stays_unfinished(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'brief.json'
            profiles.create_brief(profiles.get_profile('education-course'), self.args(path))
            value = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(value['title'], '测试片名')
            self.assertEqual(value['output_specs'], {'duration_seconds': 90.0, 'aspect_ratio': '2.35:1', 'fps': 25.0})
            self.assertIsNone(value['purpose'])
            self.assertEqual(value['editorial_decisions']['selected_shots'], [])
            self.assertIn('requires_actual_inputs', value['status'])

    def test_existing_brief_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'brief.json'
            path.write_text('existing project decisions', encoding='utf-8')
            with self.assertRaises(FileExistsError):
                profiles.create_brief(profiles.get_profile('education-course'), self.args(path))
            self.assertEqual(path.read_text(encoding='utf-8'), 'existing project decisions')

    def test_invalid_specs_do_not_create_output(self):
        cases = [{'duration': float('nan')}, {'duration': 0}, {'fps': float('inf')},
                 {'aspect': '0:9'}, {'aspect': '16/9'}]
        with tempfile.TemporaryDirectory() as temporary:
            for case in cases:
                with self.subTest(case=case):
                    path = Path(temporary) / 'brief.json'
                    with self.assertRaises(ValueError):
                        profiles.create_brief(profiles.get_profile('education-course'), self.args(path, **case))
                    self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
