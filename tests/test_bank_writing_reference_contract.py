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

    def test_summary_editing_and_analysis_routes_remain_distinct(self):
        """Condensing a draft must not be confused with extracting its points."""
        entry = (ROOT / 'SKILL.md').read_text(encoding='utf-8')
        summary_row = next(line for line in entry.splitlines() if line.startswith('|') and '/summary-reading.md' in line)
        editing_row = next(line for line in entry.splitlines() if line.startswith('|') and '/editing-and-integration.md' in line)
        self.assertIn('提炼原文要点', summary_row)
        self.assertIn('不新增经营判断', summary_row)
        self.assertIn('压缩并改写原稿', editing_row)
        self.assertNotIn('只压缩已有内容走摘要', entry)
        self.assertIn('须选下方月报/经营分析专项', entry)
        self.assertIn('改写后的原稿为交付物', reference('editing-and-integration.md'))
        self.assertIn('根据月报数据重新判断表现', reference('summary-reading.md'))
        self.assertNotIn('已有结论的压缩仍是摘要', reference('summary-reading.md'))
        self.assertNotIn('只压缩已有结论走摘要', reference('business-analysis.md'))

    def test_combined_flow_keeps_order_and_target_fact_handoff(self):
        """Summary-to-imitation handoff must not certify or import source facts."""
        combined = reference('summary-and-imitation.md')
        stages = re.findall(r'^\d+\. \*\*(.+?)。\*\*', combined, re.MULTILINE)
        self.assertEqual(stages, ['摘要', '事实交接', '仿写', '复核与交付'])
        for target in ('summary-reading.md', 'style-imitation.md'):
            self.assertIn('<skill-root>/references/' + target, combined)
        self.assertIn('若摘要对象是范文', combined)
        self.assertIn('不能成为新稿事实来源', combined)
        self.assertIn('不等于真实性已核验', combined)
        self.assertIn('锁定已确认内容，删除项不回流', combined)
        self.assertNotIn('百分之百真实', combined)
        self.assertNotIn('## 交付与边界', combined)

    def test_uncertainty_labels_name_the_actual_gap(self):
        """Preserve the entry's brackets while refining three generic labels."""
        entry = (ROOT / 'SKILL.md').read_text(encoding='utf-8')
        for label in ('〔待核实：统计口径〕', '〔待补充：比较期数据〕', '〔待确认：审批状态〕'):
            self.assertIn(label, entry)
        for name in ('style-imitation.md', 'credit-investigation.md', 'speech-and-report.md'):
            with self.subTest(reference=name):
                body = reference(name)
                self.assertNotIn('〔待核实〕', body)
                self.assertRegex(body, r'〔待(?:核实|补充|确认)：[^〕]+〕')


if __name__ == '__main__':
    unittest.main()
