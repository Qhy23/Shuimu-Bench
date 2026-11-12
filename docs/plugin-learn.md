# 插件: 网络学堂(learn)

复用校园登录会话读取网络学堂(learn.tsinghua.edu.cn): 本学期课程列表, 作业三态(未交/已交未批/已批), 课件与公告; 支持选择性下载并自动归档进本地课程. 属 network 插件: **默认禁用, 启用即授权联网**; 数据缓存在本机, 之后离线可查.

不支持在线提交作业.

## 前提与启用

1. 先启用并登录[校园账户](plugin-campus.md): `plugin-enable campus --yes` + `campus-login`
2. 启用学堂插件: `plugin-enable learn --yes`

会话失效时(提示"您未登录或登录失效"或 403)不会自动重登(不存密码): 重新 `campus-login` 后再刷新即可; 学堂应用会话过期时程序会自动经门户漫游重建, 通常无需干预.

## 刷新与浏览

```bash
python cli.py learn-refresh      # 全量刷新: 学期 -> 课程 -> 各课作业/课件/公告
python cli.py learn-status       # 缓存概况(零联网)
python cli.py learn-courses      # 课程列表: 课号/教师/与本地课程的关联
python cli.py learn-homework     # 全部作业三态(或 learn-homework <wlkcid> 单课)
python cli.py learn-files 2026-2027-1000000001    # 课件(含已下载标记)
python cli.py learn-notices 2026-2027-1000000001  # 公告
```

`wlkcid` 是学堂课程 id, 见 `learn-courses` 输出. 作业按 未交(含截止与补交) / 已交未批(含提交日期) / 已批(含成绩) 三组列出. 刷新是显式动作, 不自动定时执行.

## 下载

```bash
python cli.py learn-download --id <wjid>                        # 课件(默认)
python cli.py learn-download --kind homework --id <zyid> --wlkcid <wlkcid>   # 作业附件
python cli.py learn-download --kind notice  --id <ggid> --wlkcid <wlkcid>   # 公告附件
# 可选: --out DIR 指定目录, --overwrite 强制重下
```

- id 来自对应列表命令的输出; 已下载过的自动跳过(提示路径)
- 落盘位置: `<下载根目录>/<课程名>/<文件名>`, 重名自动加 `_1` 后缀; 下载根目录默认 `data/learn`, 可在 config.yaml 的 `learn.download_dir` 修改
- **自动归档**: 下载的文件以引用方式登记进关联的本地课程, 出现在左栏课程空间与文件页, 不移动不复制
- 作业/公告附件一次取第一组(作业附件/公告附件); 无附件时报错

## 课程关联

刷新时自动把学堂课程匹配到本地同名课程(匹配规则: 课名全等 > 课名前缀互含 > 本地课名含课号), 匹配失败不强制建课, 下载文件仍正常落盘, 只是不自动登记; 之后想关联, 建同名课程再刷新即可. 已关联的不会自动改判.

## 数据与隐私

- 缓存(课程/作业/课件/公告)保存在本机 `data/shuimu.db`, 刷新才联网, 平时浏览零网络
- 密码相关事宜见[校园账户登录](plugin-campus.md); 学堂动作只复用其会话 cookie
- 每次刷新与下载都留痕(`log` 可查); 部分课程刷新失败不影响其余课程, 失败明细随命令输出
