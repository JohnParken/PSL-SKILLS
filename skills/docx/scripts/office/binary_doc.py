"""Legacy entry retired: WPS-family reading is owned by the wps skill."""


def read_ms_doc(path):
    """Reject the retired API without opening the input."""
    raise ValueError("不支持读取 WPS 格式或旧二进制正文：.wps 使用 wps skill，.et/.ett 使用 et skill；请依据实际主入口定位")
