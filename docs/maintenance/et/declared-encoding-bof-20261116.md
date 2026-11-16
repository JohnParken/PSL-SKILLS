# ET 声明编码与 BOF 兼容（2026-11-16）

本轮仅使用合成数据，不接触私人样本、不安装依赖、不重打历史发布 ZIP、不提交推送。修改文本表格读取与旧 BIFF 工作表 BOF 兼容；入口参数、schema 和运行依赖保持不变。HTML 编码采用文件声明且明确未独立验证，不盲猜无声明 CSV/HTML；BIFF globals、记录边界、CodePage、公式缓存和资源限制保持严格。

新增 7 项测试方法，覆盖三个中文编码（含 GB18030 四字节字符）、前导零、声明来源/partial、http-equiv/重复相同声明、注释/script/body/过晚伪声明、无声明 CSV、冲突与不支持声明、重复属性、BOM 冲突、非法字节、既有 UTF-8/UTF-16，以及旧 globals 配 sheet 0600 的保存文字/数值、错误版本/类型/长度拒绝。预期值独立写入合成夹具，不以解析结果生成 oracle。

定向测试：test_text_tables.py 23 项及 test_wps_biff5.py 21 项全部通过。ET 全量 735 项通过（无失败/跳过）；ET strict、项目文档检查及 git diff --check 通过。按显式运行清单复制的临时只读独立安装中，两类新行为均经 et_cli.py run 验证退出 3、保存值正确；运行 PATH 为空并用审计钩子禁止访问原仓库，临时安装与合成输入已清理。该钩子不是OS级沙箱。Pylint 未安装、未执行；不得称 Error 为零。真实 WPS/Excel 原件、Linux/Windows、真实宿主和客户端显示未验收。description 人工复核仍准确，保持原文；当前入口正文、BIFF5参考、README版本与ET说明同步。

实际平台：macOS-27.2.1-arm64-arm-64bit-Mach-O；Python 3.14.10；Expat expat_2.7.4。本机默认 python3 的 Expat 不满足 XML 条件，因此本轮使用已安装 python3.14；未安装任何组件。仓库聚合验收由最终整合阶段另记，本记录不冒称全仓全绿。

## 独立复核后的收敛

独立审查发现并复现两处声明误识别：Content-Type 中 xcharset 被当成 charset，以及 Python 3.9 的 HTMLParser 将 title 内伪 meta 当标签。已改为按完整 text/html MIME 参数解析，拒绝未知/畸形编码参数，避免读取其它引号参数内部的伪 charset；并显式排除 title/textarea/script/style/xmp/iframe/noembed/noframes/plaintext 等文本上下文，不依赖新旧 HTMLParser 差异。

新增两项测试方法覆盖这些反例与正常带引号的 charset。最终 Python 3.14.10 文本定向 25 项、ET 全量 737 项全部通过，无跳过；Python 3.9.6 针对新 HTML 编码的 7 项测试通过（不涉及其不可用的 XML 分支）。最终 ET strict、项目文档检查与差异检查通过。前述 735 项和独立安装 smoke 属复核前阶段；最终独立整体验收由统一记录报告。版本保持同批次 20261116.0724.0209，未修改路由快照。
