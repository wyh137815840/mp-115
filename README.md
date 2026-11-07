# 115 AV刮削

MoviePilot V2 插件。扫描已经登录在 MoviePilot 里的 115，识别番号，从 JavBus 刮元数据，把云盘文件洗成标准番号名，并在本地生成 Emby/Jellyfin 用的 strm、nfo 和封面。

Emby 用 Unraid 上的 `http://192.168.1.9:8096`。插件刷新时走 MoviePilot 里已经保存的 Emby 地址和密钥，不会连这台 Windows 上的 Emby。

播放地址默认写成：

```text
http://192.168.1.10:8787/redirect?pickcode={pickcode}
```

跳转服务跑在这台 Windows 上。Unraid 的 8787 没有开，所以 strm 里不能写 `127.0.0.1`，否则 Emby 会去连它自己。`strm 目录` 要填 Unraid 上这台 Emby 正在扫描的库路径。

## 安装

在 MoviePilot 的插件仓库里加入本目录，或设置 `PLUGIN_LOCAL_REPO_PATHS` 指向 `D:\git\moviepilot`。同步插件源后安装「115 AV刮削」。

也可以把 `plugins.v2/av115scraper` 整个目录复制到 MoviePilot 的 `app/plugins/av115scraper`。

## 使用

1. MoviePilot 里先登录 115，存储 ID 保持 `u115`。
2. 填写 115 目录，每行一个，例如 `/AV/有码`。
3. strm 目录填 `/media/AV`。这是 Unraid Emby 里 AV 库正在扫描的路径，库 ID 是 4，类型是电影。MoviePilot 容器里要能写到这个目录。
4. 内地访问 JavBus 时填写代理。代理要让 MoviePilot 进程能连上。
5. 保持「只预览」先跑一遍。详情页会列出将要改成的文件名，此时不改 115，也不写本地文件。
6. 确认后再关掉「只预览」。

定时留空就只手动运行。打开「保存后立即运行一次」，或调用插件接口 `POST /api/v1/plugin/Av115Scraper/run`。

## 规则

- 有码：`SSIS-001`、`ssis001`、`ABP-123C`。字幕记为 `-C`，破解记为 `-UC`。
- FC2：`FC2-PPV-1234567`。
- 无码：`HEYZO-1234`、加勒比 `010115-001`、一本道 `010115_001`。
- 文件名没有番号时，用它所在文件夹的名字。
- 小于 300MB，以及文件名里带「预告 / sample / trailer / preview」的文件会跳过。
- 目标文件名或文件夹名已经存在时不覆盖。
- 扫描根目录本身不改名。一个文件夹里有两部以上正片时，只改文件名。
- 已有 `movie.nfo` 时默认只更新 strm。打开「覆盖已有 nfo 和封面」才会重刮。

本地目录形如 `F:\strm\AV\演员\SSIS-001-C\`，里面是 `SSIS-001-C.strm`、`movie.nfo`、`poster.jpg`、`fanart.jpg`。JavBus 没刮到时，仍然会写 strm 和只含番号的 nfo。

## 测试

```powershell
python -m unittest tests.test_av115 -v
```
