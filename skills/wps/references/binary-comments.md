# 二进制批注作者与引用子集

显式 include-related 时读取 PlcfandRef、PlcfandTxt 与 GrpXstAtnOwners（FIB97第4、5、36对）。按作者索引保留姓名、首字母和来源；保留正文锚点及批注故事的全局CP范围。Ref末尾CP与Txt最后CP依规范忽略，Txt倒二必须ccpAtn-1。引用标记/批注首字符必须U+0005，每个批注末尾必须CR。

作者XST名称必须唯一、少于56个UTF16码元、最多32767条；首字母固定LPXCharBuffer9只读前cch且cch≤9。保留字段按规范忽略。lTagBkmk=-1只映射零长度正文范围；其他值保留原始ID并标bookmark_not_decoded，未读取书签范围、日期或ATRDPost10属性，不推断作者时间。

仅在显式请求且CP引用通过验证时将对应U+0005输出为U+FFFC，并保持partial。默认仍拒绝该控制码。字符属性sprmCFSpec、表格深度和完整valid-selection尚未验证，因此不宣称完整批注语义。未关联控制码仍拒绝，不按字节搜索作者或批注内容。引用元数据不重复复制批注全文。

只guard故事的零条引用允许Ref为4字节、Txt为8字节且无作者表；不创建虚构批注。元数据每表4MiB、批注100000、作者32767；各表与CLX及其他已处理表由统一入口交叉检查重叠。规范来源见[binary-comments-evidence.json](binary-comments-evidence.json)。

新增测试为标准库合成样例，覆盖中文作者/首字母、CP锚点、默认拒绝、显式占位、作者越界与重复、UTF16、截断与配额。当前真实WPS附加批注区为空，没有真实非空批注独立内容对照。
