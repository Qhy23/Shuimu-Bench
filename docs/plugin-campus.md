# 插件: 校园账户登录(campus)

经 WebVPN 与清华统一认证(id.tsinghua.edu.cn)登录, 获取本人信息; 为网络学堂等后续校园服务提供会话. 属 network 插件: **默认禁用, 启用即授权联网**; 不启用不影响任何本地功能.

## 启用

```bash
python cli.py plugin-enable campus --yes   # --yes 跳过联网授权确认
```

或 GUI 右栏"校园"页签的启用按钮(弹确认框). 启用状态保存在本机, 禁用用 `plugin-disable campus`.

## 登录

**CLI**(密码交互输入, 绝不经过命令行参数):

```bash
python cli.py campus-login 20231234567     # 学号也可省略, 交互输入
python cli.py campus-status               # 会话状态与本人信息(默认联网校验)
python cli.py campus-status --no-check    # 只读本机会话, 不联网
python cli.py campus-logout               # 清除会话(设备指纹保留)
```

**GUI**: 右栏"校园"页签登录表单(密码掩码输入), 需要二次认证时弹出对话框: 选方式 -> 发送验证码 -> 输入 -> 可勾选"信任此设备".

## 二次认证(2FA)

账号开启二次认证时, 登录与门户漫游都可能要求验证(未信任设备每个应用授权都会再要一轮). 非交互场景可预置:

```bash
python cli.py campus-login --method sms --code 123456
# 方式: sms(短信) / wechat(微信) / totp(动态口令, 无需发送)
```

## 信任此设备

勾选(或 `--trust`)后, 本机指纹被记入账号的可信设备列表, 之后漫游新应用免二次认证.

- 本地保存的只有一个随机生成的设备标识(`data/campus-fingerprint`, 登出也保留), 不含隐私信息
- 服务端记录可在统一认证个人中心的可信设备管理里删除(设备名显示"水木工作台"); 修改密码通常也会使全部可信设备失效
- 想在本机"取消信任": 删除上述指纹文件即可(下次生成新标识, 等效未信任)
- 可信设备数量有服务端上限, 满了会静默失败, 不影响登录

## 数据与隐私

- **密码与验证码永不落盘, 不入留痕**, 只存在于登录瞬间的内存
- 本机持久化仅有: 会话 cookie 与本人信息缓存(`data/campus-session.json`, 权限 0600, gitignore)与设备指纹
- 登出清除会话文件, 指纹保留(下次登录维持信任)
- 登录为同步调用, 网络慢时界面短暂等待; WSL 系统时间偏差会导致 HTTPS 证书校验失败, 遇到网络异常先校时

## 通用插件入口

不依赖专属子命令, 也可以经通用入口运行(结果以 findings 表格呈现):

```bash
python cli.py plugin-run campus --set action=status
python cli.py plugin-run campus --set action=login --set username=20231234567
```

密码参数类型为 secret, 输入与留痕时自动脱敏.
