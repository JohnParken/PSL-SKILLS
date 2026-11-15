"""Repository-only text regressions; not model routing or writing-quality tests.

Keep the writing package scriptless. These checks protect known period and
example hazards; semantic review and actual host acceptance remain separate.
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1] / 'skills' / 'bank-writing'


def reference(name):
    """Read a maintained source reference, never user sample data."""
    return (ROOT / 'references' / name).read_text(encoding='utf-8')


class BankWritingReferenceContractTests(unittest.TestCase):
    """Guard source contracts that previously allowed unsafe template reuse."""

    def test_description_covers_existing_writing_tasks(self):
        """Metadata should name real supported tasks without a success guarantee."""
        entry = (ROOT / 'SKILL.md').read_text(encoding='utf-8')
        description = re.search(r'^description: (.+)$', entry, re.MULTILINE).group(1)
        self.assertLessEqual(100, len(description))
        self.assertLessEqual(len(description), 150)
        for task in ('经营分析', '讲话发言', '客户沟通', '投诉', '公关'):
            self.assertIn(task, description)
        self.assertNotIn('100%', description)
        self.assertFalse((ROOT / 'scripts').exists())

    def test_generic_periods_and_standalone_analysis_rules(self):
        """Quarterly analysis must not inherit a monthly-only comparison rule."""
        business = reference('business-analysis.md')
        self.assertIn('\n## 经营分析\n', business)
        self.assertIn('前一可比期间', business)
        self.assertIn('季度比上季度', business)
        self.assertNotIn('环比明确与上一个月比', business)
        self.assertNotIn('### 当月表现', business)
        for boundary in ('基期为零', '负基数', '个百分点', '互斥', 'ΣΔᵢ = Δ', 'P + N = Δ', '覆盖完整', '不能简单相加或平均'):
            self.assertIn(boundary, business)
        self.assertIn('环比必须上月', reference('monthly-report.md'))

    def test_common_email_has_shallow_conditional_routes(self):
        """Common structures remain available before optional long examples."""
        email = reference('email-communication.md')
        table = re.findall(r'^\| ([^|]+) \| ([^|]+) \| ([^|]+) \|$', email, re.MULTILINE)
        self.assertGreaterEqual(len(table), 8)  # Header and seven common scenarios.
        self.assertIn('需要详细填写示例时才读', email)
        self.assertIn('不因收件人是客户就叠加', email)
        for target in ('email-scenarios.md', 'customer-communication.md'):
            self.assertIn(f'<skill-root>/references/{target}', email)
        self.assertIn('未处理附件不能声称已加密', email)

    def test_examples_are_synthetic_and_do_not_bake_in_commitments(self):
        """Inspect reusable quoted bodies separately from cautionary prose."""
        scenarios = reference('email-scenarios.md')
        sections = re.split(r'^### ', scenarios, flags=re.MULTILINE)[1:]
        self.assertEqual(len(sections), 7)
        for section in sections:
            with self.subTest(scenario=section.splitlines()[0]):
                self.assertIn('**合成素材：**', section)
                self.assertIn('> ', section)
                self.assertIn('〔', section)
        bodies = '\n'.join(line for line in scenarios.splitlines() if line.startswith('> '))
        for unsafe in ('风险整体可控', '证件后6位', '身份证后6位', '锁定长期收益',
                       '已将该项目列入绿色信贷优先通道', '秒级自动重审',
                       'UCP600', 'Article 16', 'authorizes payment release', '随信附上'):
            with self.subTest(unsafe=unsafe):
                self.assertNotIn(unsafe, bodies)
        self.assertIn('审查尚未完成', bodies)
        self.assertIn('Our review is not yet complete', bodies)
        self.assertIn('授信、定价及实施安排尚未确定', bodies)


if __name__ == '__main__':
    unittest.main()
