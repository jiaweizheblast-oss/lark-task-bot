"""
考勤打卡：地理围栏 + 防作弊判定（纯函数，可脱离 DB 单测）。
诚实前提：纯网页 GPS 无法 100% 防篡改（root/越狱 + 模拟定位可伪造）。
本模块做的是「拔高门槛 + 全程留痕可查」：围栏、精度校验、不可能位移、内网/缺失信号标记。
服务器时间戳与 IP 由调用方（bot 路由）提供，不信任手机端时间。
隐私：只在打卡这一刻评估，不做后台跟踪；坐标仅管理员可见。
"""
import math

FLAG_LABELS = {
    "out_of_fence": "不在范围内",
    "no_gps": "无定位",
    "low_accuracy": "定位精度异常",
    "impossible_travel": "位移速度不可能",
    "private_ip": "内网/异常 IP",
}


def haversine_m(lat1, lng1, lat2, lng2):
    """两点球面距离（米）。"""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def evaluate(lat, lng, accuracy, site, prev_lat=None, prev_lng=None,
             secs_since_prev=None, ip="", max_accuracy_m=200, max_speed_kmh=900):
    """
    返回 (distance_m|None, within_fence|None, flags:list)。
    site: dict {lat,lng,radius_m} 或 None（没绑点位就只留痕不判围栏）。
    """
    flags = []
    dist = None
    within = None
    has_gps = (lat is not None and lng is not None)
    if not has_gps:
        flags.append("no_gps")
    if has_gps and site:
        dist = int(round(haversine_m(lat, lng, site["lat"], site["lng"])))
        within = dist <= int(site.get("radius_m") or 200)
        if not within:
            flags.append("out_of_fence")
    if accuracy is not None and (accuracy <= 0 or accuracy > max_accuracy_m):
        flags.append("low_accuracy")
    if (has_gps and prev_lat is not None and prev_lng is not None
            and secs_since_prev is not None and secs_since_prev > 0):
        d = haversine_m(lat, lng, prev_lat, prev_lng)
        speed_kmh = (d / secs_since_prev) * 3.6
        if speed_kmh > max_speed_kmh:
            flags.append("impossible_travel")
    # 注：IP 仅留痕存库供审计，不自动标记——生产环境代理/内网 LB 可能导致误判。
    # 想做「IP 城市 vs GPS 城市」交叉核对，需接入 IP 地理库后另加。
    return dist, within, flags


def flags_text(flags):
    """把标记转成中文短语（逗号分隔）。"""
    return "，".join(FLAG_LABELS.get(f, f) for f in flags)
