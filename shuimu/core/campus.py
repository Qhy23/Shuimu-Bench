"""校园统一认证登录协议: 两阶段登录事务, 2FA, 门户漫游与会话管理.

阶段一: WebVPN + 新统一认证(id.tsinghua.edu.cn)
  GET webvpn/login?oauth_login=true -> 302 链落登录表单页 -> 解析 SM2 公钥
  -> POST /do/off/ui/auth/login/check (i_pass = "04" + SM2(密码))
  -> (可能) 二次认证 -> "登录成功。正在重定向到" + <a href> -> 跟 302.
阶段二: 漫游进 INFO 门户
  GET id form 入口(可能走信任设备免密) -> 再提交 -> href 经
  oauth/lb-auth/lbredirect 包装 -> GET 建立 INFO 会话 -> 取 XSRF ->
  grjbxx 接口取本人信息并核对学号.

协议行为参考 thu-info-app 的 thu-info-lib(BSL-1.1) 逆向结论, 全部代码
自写. 服务端改版风险: URL/判定串集中在本模块常量区; 页面不可识别抛
ProtocolChangedError, 不崩溃.

零自动联网: 本模块只在被显式调用(用户点登录/刷新)时发请求.
"""

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from shuimu.core.campus_net import (
    CookieJar,
    Transport,
    TransportError,
    encode_form,
)
from shuimu.core.sm2 import sm2_encrypt

# ---- 协议常量 (服务端改版时单点维护) ----

ID_HOST = "https://id.tsinghua.edu.cn"
WEBVPN_LOGIN_URL = "https://webvpn.tsinghua.edu.cn/login"
LOGIN_ENTRY_URL = WEBVPN_LOGIN_URL + "?oauth_login=true"
CHECK_URL = ID_HOST + "/do/off/ui/auth/login/check"
ROAM_FORM_URL = ID_HOST + "/do/off/ui/auth/login/form/10000ea055dd8d81d09d5a1ba55d39ad"
LB_REDIRECT_URL = "https://oauth.tsinghua.edu.cn/lb-auth/lbredirect"
DOUBLE_AUTH_URL = ID_HOST + "/b/doubleAuth/login"
SAVE_FINGER_URL = ID_HOST + "/b/doubleAuth/personal/saveFinger"
XSRF_URL = ("https://webvpn.tsinghua.edu.cn/wengine-vpn/cookie?method=get"
            "&host=info.tsinghua.edu.cn&scheme=https"
            "&path=/f/info/gxfw_fg/common/index")
GRJBXX_URL = ("https://webvpn.tsinghua.edu.cn/https/"
              "77726476706e69737468656265737421f9f9479369247b59700f8"
              "1b9991b2631506205de/b/info/gxfw_fg/common/grjbxx")
DEVICE_NAME = "水木工作台"
MAX_REDIRECT_HOPS = 10
SUCCESS_MARK = "登录成功。正在重定向到"
TWO_FACTOR_MARK = "二次认证"

# grjbxx 已知字段 -> 中文标签 (字段名待真实校准, 未知字段原样展示)
_PROFILE_LABELS = (("xm", "姓名"), ("ryh", "学号"), ("dwmc", "单位"),
                   ("yxsmc", "院系"), ("rxnf", "入学年份"))
_PROFILE_MAX_FIELDS = 10


class CampusError(Exception):
    """登录流程可预期失败(消息可直接展示给用户)."""


class ProtocolChangedError(CampusError):
    """页面结构与已知协议不符, 疑似学校服务端改版."""


# ---- 结果数据 (纯数据, 可序列化) ----

@dataclass
class TwoFactorOptions:
    """二次认证可选方式(来自 FIND_APPROACHES 响应)."""

    has_wechat: bool = False
    phone_masked: str = ""
    has_totp: bool = False

    def methods(self) -> list:
        """[(值, 标签)]; 值为 SEND_CODE 的 type / totp."""
        result = []
        if self.phone_masked:
            result.append(("sms", f"短信 {self.phone_masked}"))
        if self.has_wechat:
            result.append(("wechat", "微信"))
        if self.has_totp:
            result.append(("totp", "动态口令(TOTP)"))
        return result


@dataclass
class CampusProfile:
    """本人信息(grjbxx 防御式解析)."""

    username: str
    name: str
    raw: dict = field(default_factory=dict)

    def display_fields(self) -> list:
        """[(标签, 值)]: 已知字段优先, 其余短字符串字段原样补充."""
        fields = [(label, str(self.raw[key]))
                  for key, label in _PROFILE_LABELS if key in self.raw]
        known = {key for key, _ in _PROFILE_LABELS}
        for key, value in self.raw.items():
            if len(fields) >= _PROFILE_MAX_FIELDS:
                break
            if key in known or not isinstance(value, str):
                continue
            text = value.strip()
            if text and len(text) <= 64:
                fields.append((key, text))
        return fields


@dataclass
class LoginOutcome:
    """一次登录尝试的结果; state 决定 UI 呈现与后续动作."""

    state: str    # success|need_2fa|bad_credentials|cancelled|
                  # network_error|protocol_error|failed
    message: str
    options: TwoFactorOptions = None   # state=need_2fa 时
    profile: CampusProfile = None      # state=success 时


# ---- HTML 解析 (正则, 防御式) ----

def extract_sm2_key(html: str):
    """登录页 <div id="sm2publicKey"> 文本; 无则 None."""
    found = re.search(r'id=["\']sm2publicKey["\'][^>]*>(.*?)<',
                      html, re.S)
    if found is None:
        found = re.search(r'<div[^>]*id=["\']sm2publicKey["\'][^>]*>'
                          r'([^<]+)</div>', html, re.S)
    if found is None:
        return None
    text = found.group(1).strip()
    return text or None


def extract_msg(html: str) -> str:
    """错误提示 #msg_note 文本(内层标签清理); 无则空串."""
    found = re.search(
        r'id=["\']msg_note["\'][^>]*>(.*?)</(?:span|div|p)', html,
        re.S | re.I)
    if found is None:
        return ""
    return re.sub(r"<[^>]+>", "", found.group(1)).strip()


def extract_redirect_href(html: str):
    """页面首个 <a href>; 无则 None."""
    found = re.search(r'<a[^>]+href=["\']([^"\']+)["\']', html)
    return found.group(1) if found else None


def find_fingerprint_form(html: str):
    """信任设备自动提交表单 -> (action, [(name, value)]); 无则 None."""
    for tag in re.finditer(r"<form\b([^>]*)>(.*?)</form>", html, re.S | re.I):
        attrs, inner = tag.group(1), tag.group(2)
        if not re.search(r'name=["\']fingerPrint["\']', inner):
            continue
        action_match = re.search(r'action=["\']([^"\']*)["\']', attrs)
        fields = []
        for inp in re.finditer(r"<input\b([^>]*)>", inner, re.I):
            name_match = re.search(r'name=["\']([^"\']+)["\']', inp.group(1))
            if name_match is None:
                continue
            value_match = re.search(r'value=["\']([^"\']*)["\']', inp.group(1))
            fields.append((name_match.group(1),
                           value_match.group(1) if value_match else ""))
        return (action_match.group(1) if action_match else "",
                fields)
    return None


def webvpn_wrap(url: str) -> str:
    """oauth 域名直用; 其余经 lbredirect 包装(query 不转义, 对齐服务端)."""
    if "oauth.tsinghua.edu.cn" in url:
        return url
    parts = urlsplit(url)
    port = parts.port or ("443" if parts.scheme == "https" else "80")
    uri = parts.path + (f"?{parts.query}" if parts.query else "") \
        + (f"#{parts.fragment}" if parts.fragment else "")
    return (f"{LB_REDIRECT_URL}?scheme={parts.scheme}&host={parts.hostname}"
            f"&port={port}&uri={uri}")


def _is_webvpn_login(url: str) -> bool:
    """落点是否为 WebVPN 登录页(登录失败的标志)."""
    parts = urlsplit(url)
    return parts.netloc == "webvpn.tsinghua.edu.cn" and parts.path == "/login"


# ---- 会话持久化 ----

class SessionStore:
    """data/ 下会话 JSON + 独立指纹文件(登出保留, 信任设备跨登录稳定)."""

    def __init__(self, path: str, fingerprint_path: str):
        self._path = path
        self._fingerprint_path = fingerprint_path

    def load(self):
        """读会话; 缺失/损坏返回 None."""
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def save(self, session: dict) -> None:
        """原子写 + 0o600; OSError 上抛调用方."""
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(session, f, ensure_ascii=False, indent=1)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self._path)

    def clear_session(self) -> None:
        """删除会话文件(指纹保留)."""
        try:
            os.remove(self._path)
        except OSError:
            pass

    def fingerprint(self) -> str:
        """稳定设备指纹: 读; 无则生成并持久化(32 位 hex)."""
        try:
            with open(self._fingerprint_path, encoding="utf-8") as f:
                text = f.read().strip()
            if text:
                return text
        except OSError:
            pass
        generated = uuid.uuid4().hex
        os.makedirs(os.path.dirname(self._fingerprint_path) or ".",
                    exist_ok=True)
        with open(self._fingerprint_path, "w", encoding="utf-8") as f:
            f.write(generated)
        return generated


# ---- 协议客户端 ----

class CampusClient:
    """一次登录事务的协议执行器(每事务新建; cookie 由外部 jar 承载)."""

    def __init__(self, transport: Transport, jar: CookieJar,
                 fingerprint: str, on_step=None):
        self._transport = transport
        self._jar = jar
        self._fingerprint = fingerprint
        self._on_step = on_step

    def _fetch(self, url: str, method: str = "GET", body: str = None):
        """单跳请求: 注入 Cookie 头, 回收 Set-Cookie, 触发调试钩子."""
        headers = {}
        cookie = self._jar.header_for(url)
        if cookie:
            headers["Cookie"] = cookie
        response = self._transport.fetch(url, method=method, body=body,
                                         headers=headers)
        self._jar.store_from_response(url, response)
        if self._on_step:
            self._on_step(method, url, response)
        return response

    def _follow(self, url: str, method: str = "GET", body: str = None):
        """手动跟重定向: 301/302/303 的 POST 降级 GET; <=MAX_HOPS 跳."""
        for _hop in range(MAX_REDIRECT_HOPS):
            response = self._fetch(url, method=method, body=body)
            if response.status not in (301, 302, 303, 307, 308):
                return response
            location = response.header("Location")
            if not location:
                return response
            if location.startswith("/"):
                parts = urlsplit(url)
                location = f"{parts.scheme}://{parts.netloc}{location}"
            if response.status in (301, 302, 303) and method == "POST":
                method, body = "GET", None
            url = location
        raise ProtocolChangedError(f"重定向超过 {MAX_REDIRECT_HOPS} 跳")

    # -- 登录 --

    def login(self, username: str, password: str) -> LoginOutcome:
        """阶段一完整登录; need_2fa 时先取可选方式再返回."""
        if not str(username).strip().isdigit():
            return LoginOutcome("bad_credentials", "学号须为纯数字")
        self._jar.clear()
        entry = self._follow(LOGIN_ENTRY_URL)
        response = self._entry_login(entry.text, username, password)
        if TWO_FACTOR_MARK in response:
            return self._two_factor_options()
        return self._complete_login(response, username, password)

    def _entry_login(self, entry_html: str, username: str,
                     password: str) -> str:
        """入口页 -> check 响应文本; 免密被拒且持有密码时回退凭据提交."""
        public_key = extract_sm2_key(entry_html)
        if public_key is not None:
            return self._post_credential(username, password, public_key)
        form = find_fingerprint_form(entry_html)
        if form is None:
            raise ProtocolChangedError(
                "登录入口页无法识别(无 SM2 公钥也无信任设备表单)")
        action, fields = form
        pairs = [(name, self._fingerprint if name == "fingerPrint" else value)
                 for name, value in fields]
        response = self._fetch(urljoin(ID_HOST, action), "POST",
                               encode_form(pairs))
        if extract_sm2_key(response.text) is None:
            return response.text     # 免密成功, 响应与 check 同形态
        public_key = extract_sm2_key(response.text)
        if password:
            return self._post_credential(username, password, public_key)
        raise CampusError("设备指纹未被信任, 且无密码可用")

    def _post_credential(self, username: str, password: str,
                         public_key: str) -> str:
        """提交凭据表单(字段序即协议要求, 勿调整)."""
        form = [
            ("i_user", str(username)),
            ("i_pass", "04" + sm2_encrypt(
                str(password).encode("utf-8"), public_key)),
            ("fingerPrint", self._fingerprint),
            ("fingerGenPrint", ""),
            ("i_captcha", ""),
            ("singleLogin", "on"),
            ("deviceName", DEVICE_NAME),
        ]
        response = self._fetch(CHECK_URL, "POST", encode_form(form))
        return response.text

    def _complete_login(self, response: str, username: str,
                        password: str) -> LoginOutcome:
        """check 响应 -> 成功页判定 -> callback 链 -> 漫游 -> 本人信息."""
        if SUCCESS_MARK not in response:
            return LoginOutcome("bad_credentials",
                                extract_msg(response) or "登录失败, 请稍后重试")
        href = extract_redirect_href(response)
        if href is None:
            raise ProtocolChangedError("登录成功页缺少跳转链接")
        final = self._follow(href)
        if _is_webvpn_login(final.url):
            return LoginOutcome("bad_credentials",
                                "登录失败(被重定向回登录页), 请检查账号密码")
        self._roam(username, password)
        profile = self._fetch_profile(username)
        if profile is None:
            raise ProtocolChangedError("登录后无法获取本人信息")
        return LoginOutcome("success", f"已登录: {profile.name}({username})",
                            profile=profile)

    def _roam(self, username: str, password: str) -> None:
        """阶段二: 漫游进 INFO 门户建立会话."""
        entry = self._follow(ROAM_FORM_URL)
        response = self._entry_login(entry.text, username, password)
        if TWO_FACTOR_MARK in response:
            raise ProtocolChangedError("漫游阶段出现二次认证(未预期)")
        if SUCCESS_MARK not in response:
            raise CampusError(extract_msg(response) or "门户漫游失败")
        href = extract_redirect_href(response)
        if href is None:
            raise ProtocolChangedError("漫游成功页缺少跳转链接")
        self._follow(webvpn_wrap(href))

    # -- 二次认证 --

    def _two_factor_options(self) -> LoginOutcome:
        data = self._double_auth({"action": "FIND_APPROACHES"})
        info = data.get("object") or {}
        options = TwoFactorOptions(
            has_wechat=bool(info.get("hasWeChatBool")),
            phone_masked=str(info.get("phone", "")),
            has_totp=bool(info.get("hasTotp")))
        return LoginOutcome("need_2fa", "需要二次认证", options=options)

    def _double_auth(self, form: list) -> dict:
        response = self._fetch(DOUBLE_AUTH_URL, "POST", encode_form(form))
        try:
            data = json.loads(response.text)
        except ValueError as exc:
            raise ProtocolChangedError("二次认证响应不是 JSON") from exc
        if not isinstance(data, dict) or data.get("result") != "success":
            raise CampusError(
                str((data or {}).get("msg") or "二次认证失败"))
        return data

    def send_code(self, method: str) -> None:
        """发送验证码(totp 无需发送)."""
        self._double_auth({"action": "SEND_CODE", "type": method})

    def verify_2fa(self, method: str, code: str, trust: bool = False,
                   username: str = "", password: str = "") -> LoginOutcome:
        """提交验证码完成登录(VERITY 拼写为服务端原样)."""
        action = "VERITY_TOTP_CODE" if method == "totp" else "VERITY_CODE"
        data = self._double_auth(
            {"action": action, "vericode": str(code)})
        redirect = str((data.get("object") or {}).get("redirectUrl", ""))
        if not redirect:
            raise ProtocolChangedError("二次认证响应缺 redirectUrl")
        if trust:
            self._try_save_finger()
        response = self._fetch(urljoin(ID_HOST, redirect))
        return self._complete_login(response.text, username, password)

    def _try_save_finger(self) -> bool:
        """信任此设备; 失败(含数量上限)容忍, 不阻断登录."""
        response = self._fetch(SAVE_FINGER_URL, "POST", encode_form([
            ("fingerprint", self._fingerprint),
            ("deviceName", DEVICE_NAME),
            ("radioVal", "是"),
        ]))
        try:
            data = json.loads(response.text)
            return data.get("result") == "success"
        except ValueError:
            return False

    # -- 会话与本人信息 --

    def _xsrf_token(self) -> str:
        response = self._fetch(XSRF_URL)
        found = re.search(r"XSRF-TOKEN=(.+?);", response.text + ";")
        if found is None:
            raise CampusError("无法获取 XSRF 令牌(会话可能已失效)")
        return found.group(1)

    def _fetch_profile(self, username: str):
        token = self._xsrf_token()
        response = self._follow(f"{GRJBXX_URL}?_csrf={token}")
        if response.status != 200:
            return None
        try:
            data = json.loads(response.text)
        except ValueError:
            return None
        info = data.get("object") if isinstance(data, dict) else None
        if not isinstance(info, dict) or \
                str(info.get("ryh", "")) != str(username):
            return None
        return CampusProfile(username=str(username),
                             name=str(info.get("xm") or username), raw=info)

    def check_session(self, username: str) -> CampusProfile:
        """校验既有会话并返回本人信息; 失效返回 None."""
        return self._fetch_profile(username)

    def session_data(self, username: str, profile: CampusProfile) -> dict:
        """登录成功后的会话持久化数据(不含任何密码)."""
        now = time.time()
        return {"version": 1, "username": str(username),
                "cookies": self._jar.as_dict(),
                "profile": {"username": profile.username, "name": profile.name,
                            "raw": profile.raw},
                "checked_at": now, "saved_at": now}


# ---- 顶层编排 (controller 与插件共用) ----

def perform_login(store: SessionStore, transport_factory, username: str,
                  password: str, ask_2fa=None,
                  on_step=None) -> LoginOutcome:
    """完整登录事务; ask_2fa(options, send_code) -> (方式, 验证码, 信任)|None.

    密码与验证码仅存在于本次调用栈, 不落盘不留痕. ask_2fa 实现方自行
    决定是否调用 send_code(GUI 发送按钮/CLI 选择后发送; totp 无需发送;
    非交互场景假定验证码已具备).
    """
    jar = CookieJar()
    client = CampusClient(transport_factory(), jar, store.fingerprint(),
                          on_step)
    try:
        outcome = client.login(username, password)
        if outcome.state == "need_2fa":
            if ask_2fa is None:
                return outcome
            answer = ask_2fa(outcome.options, client.send_code)
            if answer is None:
                return LoginOutcome("cancelled", "已取消二次认证")
            method, code, trust = answer
            outcome = client.verify_2fa(method, code, trust=trust,
                                        username=username, password=password)
        if outcome.state == "success":
            store.save(client.session_data(username, outcome.profile))
        return outcome
    except TransportError as exc:
        return LoginOutcome("network_error", f"网络异常: {exc}")
    except ProtocolChangedError as exc:
        return LoginOutcome("protocol_error",
                            f"{exc}(学校认证页面可能已改版, 请反馈)")
    except CampusError as exc:
        return LoginOutcome("failed", str(exc))
