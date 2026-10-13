# WARP-Unlock

通过 Cloudflare WARP 为 VPS 提供指定网站的出口分流。当前主入口 `run.sh` 使用 **SmartDNS + ipset + iptables/ip6tables + WireGuard**，支持 IPv4、IPv6 和双栈。

流媒体是否解锁以及可访问地区取决于实际 WARP 出口和平台策略；安装成功不代表一定解锁。

## 环境要求

- 使用 apt 和 systemd 的 Debian/Ubuntu 系统；主脚本采用数字路由表 ID，以兼容 Debian 12/13 的配置方式。
- root 权限、amd64 或 arm64 架构，以及可用的内核 WireGuard 支持。
- 能访问软件源、GitHub、WARP 注册接口和隧道端点。
- 本地测试覆盖菜单、配置生成及故障模拟；真实 WARP 握手、分流和流媒体解锁仍需在允许创建网络接口的环境中验证。

## 安装

在交互式终端（例如 SSH 登录后的 shell）中执行：

```bash
curl -fsSL https://raw.githubusercontent.com/7HGRACE/WARP-Unlock/main/run.sh | bash
```

脚本会从终端读取菜单选择，支持 `curl | bash`。按 Enter 默认选择双栈，输入无效选项会重新询问；输入 EOF 会明确报错并退出。

也可以先下载再运行：

```bash
curl -fsSL https://raw.githubusercontent.com/7HGRACE/WARP-Unlock/main/run.sh -o warp-unlock.sh
bash warp-unlock.sh
```

没有交互终端（例如自动化任务、未分配 TTY 的远程命令）时必须显式指定模式：

```bash
curl -fsSL https://raw.githubusercontent.com/7HGRACE/WARP-Unlock/main/run.sh | STACK_MODE=ipv4 bash
```

**环境变量必须传给管道右侧的 `bash`**。写成 `STACK_MODE=ipv4 curl ... | bash` 只会将变量传给 curl。

| 选项 | STACK_MODE | 行为 |
| --- | --- | --- |
| 1 | `dual` | IPv4 和 IPv6 指定网站分流；另添加高 metric IPv6 默认路由，见下文 |
| 2 | `ipv4` | 只配置 IPv4 分流，WireGuard 地址仅保留 IPv4 |
| 3 | `ipv6` | 只配置 IPv6 分流，WireGuard 地址仅保留 IPv6 |
| 4 | `none` | 普通 WireGuard 模式：不添加自定义分流 hook，但仍修改 DNS、启动隧道，并由 wg-quick 自动配置路由 |

`none` **不是仅生成文件的模式**。它保留 wgcf 的双栈配置及 AllowedIPs，可能接管双栈默认出口。`ipv4` 模式也不会禁用系统原有 IPv6、屏蔽 AAAA 记录或强制应用优先 IPv4。

可选环境变量：

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `STACK_MODE` | 交互选择 | `dual` / `ipv4` / `ipv6` / `none` |
| `WG_IF` | `wg` | WireGuard 接口名 |
| `WGCF_VERSION` | `latest` | wgcf 版本，可固定版本号 |
| `WARP_ENDPOINT` | wgcf 生成值 | 覆盖隧道端点，例如 `162.159.193.1:2408` |
| `ROUTE_TABLE_ID` | `250` | 分流路由表编号 |
| `MARK` | `2` | 分流数据包标记 |

## 分流原理及范围

1. SmartDNS 解析指定域名，把结果加入 IPv4 集合 `warp` 或 IPv6 集合 `warp6`。
2. iptables/ip6tables 在 OUTPUT 和 PREROUTING 匹配目标 IP 并设置 mark。
3. 策略路由将带标记的流量送入专用路由表，经 WireGuard 发往 WARP。
4. hook 配置 NAT 和 TCP MSS 调整；系统服务负责开机启动。

默认包含 Netflix、Google/YouTube 的部分域名，以及 `ipinfo.io`、`ip.sb`、`cloudflare.com` 等测试域名。准确列表见 `run.sh` 中的 `write_smartdns_conf`。可编辑 `/etc/smartdns/smartdns.conf` 后执行 `systemctl restart smartdns`；重新运行安装脚本会重写该配置（旧配置会备份）。

默认上游为普通 DNS `1.1.1.1` 和 `8.8.8.8`，使用 Debian SmartDNS 46.1 支持的 `server` 指令，并关闭双栈地址优选以分别处理 A/AAAA。配置不依赖 QUIC/H3 或仅用于 bootstrap 的上游。需要加密 DNS 时可按实际 SmartDNS 版本配置受支持的上游，并重新验证解析。

分流实际按 IP 匹配。绕过 SmartDNS 的应用、自带 DoH 的客户端或直接访问 IP 的连接，可能无法触发规则；共享同一 IP 的其他网站也可能被分流。作为网关使用时，还需要自行确保客户端 DNS 路径、转发防火墙及相关网络配置正确。

**IPv6 默认路由：** `dual` 和 `ipv6` 模式还会向主路由表添加 `default dev wg metric 4096`，用于绑定接口的 IPv6 连通性测试。如果没有更优的 IPv6 默认路由，未列入分流规则的 IPv6 流量也可能使用 WARP。因此这两个模式并非严格的“仅指定域名走 WARP”。

## 系统修改

- 安装 WireGuard、wgcf、SmartDNS 及所需工具；分流模式还安装 ipset/iptables。
- 停用已有 dnsmasq 和 systemd-resolved，重写 `/etc/resolv.conf`，配置本机 DNS 及公共备用 DNS。备用 DNS 不负责填充分流集合。
- 备份已有 SmartDNS 主配置并重写；写入 `/etc/wireguard/<接口>.conf`，复用已有 wgcf 账号。
- 分流模式写入 `/etc/wireguard/up`、`/etc/wireguard/down` 以及 `/etc/sysctl.d/99-warp-unlock.conf`，开启对应地址族转发。
- 启用并重启 SmartDNS 和 `wg-quick@<接口>`。

已有自定义 DNS、防火墙或策略路由的机器应检查这些配置是否冲突。hook 文件、集合和链名是固定的，不能仅靠修改 `WG_IF` 部署多套互不干扰的实例。重复运行或切换模式也不等同于自动迁移旧规则。

## 检查与排错

安装后的关键自检包括：服务运行及开机启动状态、指定接口是否存在、DNS 的 NOERROR 状态和实际地址、所选地址族的策略规则/默认路由、DNS 地址是否进入 ipset、标记及 NAT 规则、绑定 WARP 接口的 HTTP 出口探测，以及是否发生过 WireGuard 握手。`none` 模式检查双栈 DNS 和隧道出口，不要求自定义分流规则。

任一关键检查失败，脚本会汇总错误、返回非零退出码，并且不输出 `Done`。例如 `dig` 即使退出码为 0，也可能返回 SERVFAIL；自检会检查响应内容。HTTP 探测启用错误状态检测及总超时。检查失败不会自动回滚已安装配置，不能把非零退出码视作“系统未发生修改”。这些检查不代表已验证网关转发或流媒体解锁。

默认接口名为 `wg`；自定义接口时相应替换：

```bash
systemctl status smartdns wg-quick@wg --no-pager
journalctl -u smartdns -u wg-quick@wg -n 100 --no-pager
wg show
dig @127.0.0.1 youtube.com A
dig @127.0.0.1 youtube.com AAAA
```

IPv4 分流模式下：

```bash
ipset list warp
ip rule show
ip route show table 250
iptables -t mangle -vnL fwmark
```

IPv6 分流模式下：

```bash
ipset list warp6
ip -6 rule show
ip -6 route show table 250
ip -6 route show default
ip6tables -t mangle -vnL fwmark6
```

旧版本出现“显示菜单后直接退出”，是因为 `read` 从 `curl | bash` 的标准输入读取，遇到 EOF 后被 `set -e` 终止。请使用本仓库最新脚本；非交互环境使用前述 `STACK_MODE=... bash` 命令。

## 停用与恢复

停用默认 WARP 服务：

```bash
systemctl disable --now wg-quick@wg
```

这不是完整卸载。DNS、转发设置、安装的软件及账号文件仍会保留。恢复时应按安装前实际配置还原 DNS 服务和 `/etc/resolv.conf`，再停用 SmartDNS；同时检查转发设置及遗留路由。不要在仍使用 `127.0.0.1` 解析时直接关闭 SmartDNS。脚本目前没有完整卸载或自动回滚功能。

## 其他文件

| 文件 | 用途 |
| --- | --- |
| `run-boringtun.sh` | 旧版用户态 WireGuard 安装流程，仍依赖旧上游资源和固定版本，未同步主脚本功能 |
| `dnsmasq/`、`scripts/` | 旧版 dnsmasq 配置和分流 hook，主脚本会自行生成 SmartDNS 配置及 hook |
| `switch.sh` | 独立 Netflix 检测/重连脚本，需手动配置地区和 Telegram 信息；重连不保证更换 IP 或地区 |
| `registeer/warp-register.py` | 独立 WARP 注册及 WireGuard 配置生成工具 |
| `nft-routingid.sh` | 独立 RoutingID 数据包改写工具 |

以上独立工具不会由当前主脚本自动启用。

## 本地回归测试

```bash
python3 -m unittest discover -s tests -v
```

测试提取脚本函数，通过伪终端检查菜单，在临时目录检查四种模式、单栈逗号/多行地址、Endpoint 覆盖及配置权限，并模拟 DNS SERVFAIL、空响应、缺失路由/标记/ipset、无握手和所有关键命令失败。不会运行安装器或更改系统网络。

可选的真实 SmartDNS 测试会启动独立前台进程，把生成配置的监听端口、日志及默认上游重定向到临时文件和本机高端口，使用本机 UDP 上游返回 A/AAAA。它无需外网、WireGuard 或 root；未找到 SmartDNS 时明确跳过，不算验证通过。可指定 Debian 13 官方 46.1 的已安装或解包后的二进制：

```bash
SMARTDNS_BIN=/path/to/smartdns python3 -m unittest discover -s tests -v
```

复测时请记录 SmartDNS 版本、测试通过/跳过项目和退出码，并单独说明是否验证真实公网 DNS、WARP 握手、IPv4/IPv6 分流及流媒体访问。禁止创建网络接口的环境只能验证配置和模拟结果，不能据此宣称隧道正常可用。

项目源自 [acacia233/Project-WARP-Unlock](https://github.com/acacia233/Project-WARP-Unlock)。

