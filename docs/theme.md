# 界面主题

主窗口标题栏的齿轮按钮可切换界面主题. 内置"深色"与"浅色"两套主题,
切换立即生效, 不需要重启.

## 持久化默认主题

在 `config.yaml` 的 `app` 段填写主题 id; 用户数据目录的 `config.yaml`
优先级更高, 适合保存本机选择. 界面内切换只影响当前会话, 不回写配置.

```yaml
app:
  theme: light   # dark(默认) / light / 用户主题文件 id
```

字号仍由 `app.font_size` 设置, 运行时可按 `Ctrl+=` / `Ctrl+-` 调整,
`Ctrl+0` 重置.

## 自定义主题

在以下目录创建 `<主题id>.yaml`, 文件名(不含扩展名)就是主题 id:

- Linux: `~/.local/share/shuimu/themes/`
- Windows: `%APPDATA%/ShuimuBench/themes/`

齿轮菜单的"打开主题目录..."可直接打开该目录. 文件示例:

```yaml
name: 海蓝
extends: dark       # 继承的内置主题, 可写 dark 或 light, 默认 dark
colors:             # 只覆盖需要修改的 token, 其余继承
  accent: "#0b5cad"
  accent_hover: "#1473c7"
  accent_press: "#084683"
  selection: "#0b3d66"
  focus: "#1473c7"
```

重启后新主题会出现在齿轮菜单中, 也可把 `app.theme` 设为该文件的主题 id.
支持的颜色 token:

| 分组 | token |
|---|---|
| 背景与文字 | `base`, `panel`, `elevated`, `hover`, `border`, `fg`, `fg_dim` |
| 主色与交互 | `accent`, `accent_hover`, `accent_press`, `accent_fg`, `selection`, `focus` |
| 语义色 | `danger`, `danger_press`, `warn`, `success` |

颜色使用 `#rrggbb` 或 `#rgb`. 非法颜色只忽略对应项; 未知 token 忽略并告警;
损坏文件跳过, 不影响其他主题与程序启动. 同名用户主题可以覆盖内置主题.
