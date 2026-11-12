# 115 AV刮削

MoviePilot V2 插件。扫描已经登录在 MoviePilot 里的 115，识别番号，刮元数据，把云盘文件洗成标准番号名，并在本地生成 Emby/Jellyfin 用的 strm、nfo 和封面。有码和无码走 JavBus。FC2 不走 JavBus，优先用 fc2cmadb，失败再试 FC2 官网和 Wayback。

Emby 用 Unraid 上的 `http://192.168.1.9:8096`。插件刷新时走 MoviePilot 里已经保存的 Emby 地址和密钥，不会连这台 Windows 上的 Emby。

播放分内网和外网，都走本插件的 `/redirect`，由它再 302 到 115 直链。P115StrmHelper 不在这个仓库里，本插件不改它的配置。

| 配置键 | 界面 | 默认 |
| --- | --- | --- |
| `play_base` | 外网播放地址模板 | `https://mp.yuanhot.top/api/v1/plugin/Av115Scraper/redirect?pickcode={pickcode}&file_name={filename}` |
| `play_base_lan` | 内网播放地址模板 | `http://192.168.1.9:3000/api/v1/plugin/Av115Scraper/redirect?pickcode={pickcode}&file_name={filename}` |
| `lan_networks` | 内网网段 | `192.168.0.0/16,10.0.0.0/8,172.16.0.0/12,127.0.0.0/8,::1/128,fc00::/7,fe80::/10` |
| `strm_use_lan` | strm 写内网地址 | 开 |

这四个键是 Av115 自己的。Anime115 如果要做同样的分流，沿用这些名字，不要另起 `lan_base` 或 `moviepilot_address`。

「strm 写内网地址」打开时，新 strm 内容是内网地址。家里的 Emby 直接连 `http://192.168.1.9:3000`，不会去打开 `https://mp.yuanhot.top`。关掉之后，strm 改写外网地址，公网好友的播放器能直接打开；内网客户端打到这个外网入口时，插件看 `X-Forwarded-For`（没有则看直连地址），确认是内网就把请求 302 到内网地址，115 直链在内网入口获取。外网客户端打到内网地址时，则 302 到外网地址。

公网反代要保留原始域名（`X-Forwarded-Host` 或 `Host`）并带上 `X-Forwarded-For`。反代如果把 `Host` 改成内网地址、又不写原始域名，插件不会把这个请求再 302 回公网，避免来回跳。

`strm 目录` 要填这台 Emby 正在扫描的库路径。不能写 `127.0.0.1`：播放器不在 MoviePilot 那台机器上。

## 更新后怎么生效

插件在 MoviePilot 里重载「115 AV刮削」即可，不用重启容器。热重载会清掉 `av115_*` 模块。

**已经写在磁盘上的 strm 不会跟着变。** 旧文件里如果还是 `https://mp.yuanhot.top/api/v1/plugin/P115StrmHelper/redirect_url?...`，内网播放仍会走外网域名。关掉「只预览」再跑一遍扫描；已有 `movie.nfo` 时默认只重写 strm，不重刮封面。

外网好友的播放器如果会自己打开 strm 里的地址，而新 strm 写的是 `192.168.1.9`，他们会连不上。关掉「strm 写内网地址」，再跑一遍扫描。

## 安装

在 MoviePilot 的插件仓库里加入本目录，或设置 `PLUGIN_LOCAL_REPO_PATHS` 指向 `D:\git\moviepilot`。同步插件源后安装「115 AV刮削」。

也可以把 `plugins.v2/av115scraper` 整个目录复制到 MoviePilot 的 `app/plugins/av115scraper`。

## 使用

详情页、配置里的输入框，以及 `POST /api/v1/plugin/Av115Scraper/task`，可以提交番号、磁力或 `ed2k://|file|...|/`。ed2k 和磁力一样进 115 离线下载。

1. MoviePilot 里先登录 115，存储 ID 保持 `u115`。
2. 填写 115 目录，每行一个，例如 `/AV/有码`。
3. strm 目录填 `/media/AV`。这是 Unraid Emby 里 AV 库正在扫描的路径，库 ID 是 4，类型是电影。MoviePilot 容器里要能写到这个目录。
4. 内地访问 JavBus 时填写代理。FC2 代理留空则沿用它。代理要让 MoviePilot 进程能连上。
5. 保持「只预览」先跑一遍。详情页会列出将要改成的文件名，此时不改 115，也不写本地文件。
6. 确认后再关掉「只预览」。

定时留空就只手动运行。打开「保存后立即运行一次」，或调用插件接口 `POST /api/v1/plugin/Av115Scraper/run`。

### 按目录扫描（1.5.11+）

不想改「115 目录」配置、只想补某几个目录时，用 `run_roots`：

```bash
curl -X POST "http://<moviepilot>:3000/api/v1/plugin/Av115Scraper/run_roots" \
  -H "X-API-KEY: <token>" -H 'Content-Type: application/json' \
  -d '{"roots":["/AV/SSIS-001","/AV/EBOD-531"]}'
```

- 走插件当前「只预览 / 覆盖已有 nfo / 刷新 Emby」配置，不改 `cloud_paths`；
- 插件内 `/AV` 等目录列表有 60 秒缓存（移动/改名/建目录时失效），多目录扫描明显更快；
- 返回 `{"ok":true,...}` 后可用 `GET /api/v1/plugin/Av115Scraper/status` 看结果。

### Emby 多版本合并（1.5.12+）

同一番号有多个 strm 时（例如 `CAWB-001` 与 `CAWB-001-UC`），Emby 会把它们显示成多个同名条目。插件新增接口可把它们合成一个条目下的多个「版本」：

```bash
curl -X POST "http://<moviepilot>:3000/api/v1/plugin/Av115Scraper/merge_versions" \
  -H "X-API-KEY: <token>"
```

- 只使用 MoviePilot 里已保存的 Emby 地址/密钥，读取 `Emby 媒体库名`（默认 `AV`）；
- 按 `javbus`/`fc2` 的 ProviderId 分组；MetaTube 仅在路径番号一致时使用，避免串号误合并；
- 内容完全相同的两个 strm 不合并（先去重）；已合并的组会跳过；
- 返回 `groups/merged/skipped_already/skipped_identical/failed`；
- 可挂在定时任务里定期执行。

## 规则

- 有码：`SSIS-001`、`ssis001`、`ABP-123C`。字幕记为 `-C`，破解记为 `-UC`。
- FC2：`FC2-PPV-1234567`。元数据不走 JavBus。资料站给出标题、封面、演员、发售日和标签；官网年龄验证或商品已删除时再试 Wayback。nfo 的 `uniqueid` 类型是 `fc2`。
- 无码：`HEYZO-1234`、加勒比 `010115-001`、一本道 `010115_001`。
- 文件名没有番号时，用它所在文件夹的名字。
- 小于 300MB，以及文件名里带「预告 / sample / trailer / preview」的文件会跳过。
- 目标文件名或文件夹名已经存在时不覆盖。
- 扫描根目录本身不改名。一个文件夹里有两部以上正片时，只改文件名。
- 已有 `movie.nfo` 时默认只更新 strm。打开「覆盖已有 nfo 和封面」才会重刮。

本地目录形如 `F:\strm\AV\演员\SSIS-001-C\`，里面是 `SSIS-001-C.strm`、`movie.nfo`、`poster.jpg`、`fanart.jpg`。来源没有刮到时，仍然会写 strm 和只含番号的 nfo。

FC2 封面是普通横图，海报不再按 JavBus 那样裁右侧。已经写过空 nfo 的目录默认会跳过。要回填时打开「覆盖已有 nfo 和封面」，只扫 FC2 目录跑一次，然后再关掉覆盖。

插件更新后，在 MoviePilot 里重载「115 AV刮削」（或重启一次）。热重载会清掉 `av115_*` 模块。可用 `FC2-PPV-4751586` 和 `FC2-PPV-3127193` 对一下标题、演员和封面。

## 测试

```powershell
python -m unittest tests.test_av115 tests.test_av115_fc2 tests.test_av115_play -v
```

联网核对上面两个番号：

```powershell
$env:AV115_FC2_LIVE = "1"
python -m unittest tests.test_av115_fc2.Fc2LiveTest -v
```
