# 生命周期契约

## 配置与归档

`ROUTER_CONFIG_VARIABLES` 支持外层 `{ "commonParam": [...] }` 或 `[{"name":"commonParam","value":...}]`；commonParam可以是列表或列表JSON字符串。通用取值函数对字符串参数值先执行 `.strip()` 去掉两端空白，保留中间内容及非字符串类型，参数名及原始环境变量不改写。唯一questionId裁剪后必须为非负整数或1–128位ASCII字母/数字/下划线/短横线字符串，拒绝布尔值、路径和设备名。重复JSON键、NaN、重复业务参数及错误结构拒绝；配置缺失/无效时跳过归档，不改主文件路径。

有效归档位置是任务目录内 `output/{questionId}/`，按主文件basename平铺，复制而非移动。批内同名（忽略大小写）拒绝；输入路径别名及硬链接保护，禁止路径组件符号链接。单文件经任务根目录 `./` 的独占临时副本、flush/fsync、权限设置后原子replace；已有同名归档会替换，保留原归档权限。不会合并目录或扫描历史。多文件不是整批事务，失败前已成功的副本保留；主文件不回滚。现有路径检查不承诺隔离同权限恶意并发写者。

环境读取保留DOCX来源的 `DOCX_DEBUG_ROUTER_CONFIG` 开关，默认向stderr记录原始配置。不要向最终用户转贴日志；无需该诊断时，在本任务环境设置为0，别修改全局环境或为了发现配置而回显变量。程序接口可用无日志的 `read_environment_parameter`。

```console
python -I -S -B "<skill_dir>/scripts/lifecycle.py" archive ./报告.txt --protect-input ./用户原件.json
```

输出JSON含archive_status、files、warnings及archive_message。退出0为copied，3为skipped，1为failed，2为参数错误；0/3/1均需读取完整JSON。原DOCX归档CLI的历史退出行为未更改。成功仅向用户展示archive_message“归档完成”，主交付物链接仍保留；失败不将内部路径泄露在用户提示中，内部警告用于定位。

## 登记、封存、清理

新plan的manifest和files只接受任务根目录 `./` 的单一文件名，directories必须为空；CLI `--directory` 保留参数但拒绝新登记。旧嵌套清单仍可seal/clean，不搬迁或补认领。所有新建临时文件直接位于 `./`，包括内部清单更新和归档暂存文件；跨文件系统发布失败时报告，不改用目标目录暂存。

先在任务目录确认输入/产物清单，且即将创建的临时路径不存在，再执行：

```console
python -I -S -B "<skill_dir>/scripts/lifecycle.py" plan task.cleanup.json --file worker.py --file spec.json --protect 用户原件.json --deliverable 报告.txt
```

业务技能创建/运行worker和spec、发布并验收报告、archive返回后，才可执行：

```console
python -I -S -B "<skill_dir>/scripts/lifecycle.py" seal task.cleanup.json --outcome success --verified --archive-status copied
python -I -S -B "<skill_dir>/scripts/lifecycle.py" clean task.cleanup.json
```

上例copied仅适用于刚取得copied；实际skipped/failed须如实传入，不伪造。成功封存要求非空主交付物、verified声明及有效归档结果，但不验证声明真实性，调用方必须可信。清理核对任务根身份、受保护输入、交付物及临时项指纹；修改、链接、多硬链接或非空目录等保守保留/失败，只删除登记项，保留manifest回执。`plan/seal/clean` 退出0成功，3保留，1失败/阻止/错误，2参数错误；结果JSON不能仅靠退出0断言业务成功。

## 失败和恢复

待重试先 `seal ... --outcome retry` 再clean只会保留材料。修正本轮材料后可对retry清单重新seal；最终封存不能重封。中断后只用原manifest恢复：尚未封存先核实状态，已封存可重复clean；缺失文件可幂等处理，身份/内容变化不重新认领。最终失败或停止可用failed/stopped封存，恢复备份通过 `--retain 路径 原因` 保留。无manifest不能补登未知旧文件。

普通plan/seal/clean不是自动生成runner，没有多进程任务锁、创建时强指纹事务、自动重试或任意脚本执行能力；同一清单仅一个可信控制者串行使用。plan证明登记时不存在，不能对抗登记后其他写者冒用路径；seal指纹不是单独的所有权证明。需自动生成/重试状态机时由业务技能集成，DOCX继续使用自己的runner。不得在临时Python自身尚未退出时clean；由外层控制者收尾。
