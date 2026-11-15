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

    def test_official_amounts_and_draft_status_keep_separate(self):
        """Different monetary concepts and an unapproved draft are not approvals."""
        body = reference('official-documents.md')
        self.assertNotIn('各品种分项额度相加必须严格相等', body)
        for boundary in ('分项互斥完整', '不混加或要求彼此相等', '报审稿可写拟议事项', '不把起草等同于已批准或已发送'):
            self.assertIn(boundary, body)

    def test_aml_records_do_not_imply_crime_or_missing_fact(self):
        """A transaction fragment supports observations, not a crime template."""
        body = reference('audit-and-compliance.md')
        aml = body.split('## 反洗钱（AML）可疑交易深度分析报告', 1)[1].split('## 短例：', 1)[0]
        self.assertNotIn('客观认定该账户涉嫌', aml)
        self.assertNotIn('数分钟内即通过网银', aml)
        for boundary in ('已读交易期间与覆盖', '实际笔数和金额', '局部样本不推全量',
                         '未提供不等于不存在或虚假', '有权主体确认', '不实际报送'):
            self.assertIn(boundary, aml)

    def test_sop_and_admission_require_actual_rules(self):
        """Operational roles and rejection criteria cannot come from examples."""
        sop = reference('banking-regulations-sop.md')
        for old_rule in ('由总行党委或高级管理层', '必须剪角作废', '主管与经办柜员双人现场'):
            self.assertNotIn(old_rule, sop)
        self.assertIn('实际层级、名称、制定与批准主体以本行制度为准', sop)
        self.assertIn('不擅自销毁凭证', sop)
        research = reference('industry-research.md')
        self.assertNotIn('存在虚假发票开具嫌疑的企业发放贷款', research)
        self.assertIn('不由行业印象或未经核实的嫌疑直接认定禁入、拒贷', research)

    def test_example_parameters_require_task_evidence(self):
        """Schedules and sample ratios must not become universal defaults."""
        cases = {
            'industry-research.md': (('占30%', '占50%', '占20%'), ('调查期间与岗位范围', '不预设比例')),
            'banking-regulations-sop.md': (('8:00—8:20', '17:30—18:00'), ('合计等于会议总时长', '不为凑时长补造安排')),
            'digital-product-requirements.md': (('第1-2月', '第3-4月', '第5月', '第6月'), ('阶段周期与验收条件', '以实际验收和审批为前提')),
            'activity-planning.md': (('15—30人', '09:00-09:30'), ('活动时段与邀约规模', '可给注明条件的建议')),
        }
        for name, (old_defaults, boundaries) in cases.items():
            with self.subTest(reference=name):
                body = reference(name)
                for old_default in old_defaults:
                    self.assertNotIn(old_default, body)
                for boundary in boundaries:
                    self.assertIn(boundary, body)

    def test_imitation_demo_does_not_add_product_capabilities(self):
        """The output example must stay inside the synthetic target material."""
        body = reference('style-imitation.md')
        output = body.split('#### 【按素材范围仿写】', 1)[1].split('\n\n', 1)[0]
        for unsupported in ('税收直连', '政务大数据', '纯信用', '电子票据', '秒贴', '200户', '30%'):
            self.assertNotIn(unsupported, output)
        for state in ('拟', '待确认', '效果须经验证'):
            self.assertIn(state, output)
        self.assertIn('不新增数据接口', body)

    def test_report_and_publicity_evidence_do_not_certify_status(self):
        """Source, audit status and advertising permission are separate checks."""
        credit = reference('credit-investigation.md')
        for boundary in ('自编不等于未审计', '待核实：报表审计状态', '仅在来源确认未经审计时', '不机械要求全套'):
            self.assertIn(boundary, credit)
        publicity = reference('pr-and-crisis-management.md')
        for boundary in ('比较范围', '统计口径与时点', '证据充分不等于用语合法', '不把权威排名当作自动许可'):
            self.assertIn(boundary, publicity)

    def test_optional_rhetoric_and_client_promises(self):
        """Rhetorical polish and empathy cannot add obligations or facts."""
        speech = reference('speech-and-report.md')
        self.assertIn('不默认套金句或口号排比', speech)
        self.assertIn('不能截断金融术语、条件或风险限定', speech)
        self.assertNotIn('每句话长度是否控制在15—25字以内', speech)
        self.assertIn('不为凑四字或整齐层级虚增工作板块', reference('comprehensive-writing.md'))
        customer = reference('customer-communication.md')
        for boundary in ('共情不等于确认未查明的原因或责任', '事实与授权依据', '保留已确认的不利事实'):
            self.assertIn(boundary, customer)

    def test_imitation_preserves_independently_supported_overlap(self):
        """Matching names or numbers cannot override independent target evidence."""
        body = reference('style-imitation.md')
        self.assertIn('本轮目标素材独立支持', body)
        self.assertIn('不因与范文重合就删改已确认事实', body)
        self.assertIn('不把已提供等同于真实', body)
        self.assertNotIn('姓名严禁出现在新稿件中', body)
        self.assertNotIn('点差绝不可带入新文', body)

    def test_planning_roles_do_not_imply_deployed_capabilities(self):
        """A service or system example cannot establish authority or deployment."""
        cases = {
            'activity-planning.md': ('未确认只列为建议', '不默认可用或承诺授信结果、办理速度'),
            'digital-product-requirements.md': ('不默认已部署接口', '不由角色名称推定自动审批', '不统一规定受托支付'),
            'industry-research.md': ('已部署能力写事实，拟建设写建议', '不预设全员标配'),
        }
        for name, boundaries in cases.items():
            with self.subTest(reference=name):
                body = reference(name)
                for boundary in boundaries:
                    self.assertIn(boundary, body)
        self.assertNotIn('流动服务小分队现场办公、移动开卡、快速测额授信', reference('activity-planning.md'))
        self.assertNotIn('为客户经理标配平板移动作业终端', reference('industry-research.md'))

    def test_period_requirements_and_speech_structure_are_consistent(self):
        """Document templates must preserve available periods and actual structure."""
        self.assertIn('缺期列明，不默认具备三年数据或已完成审计', reference('official-documents.md'))
        speech = reference('speech-and-report.md')
        self.assertNotIn('三段九扣', speech)
        outline = re.search(r'```text\n(.*?)\n```', speech, re.DOTALL).group(1)
        self.assertEqual(len(re.findall(r'^【', outline, re.MULTILINE)), 4)
        self.assertIn('### 综合讲话四段结构', speech)


if __name__ == '__main__':
    unittest.main()
