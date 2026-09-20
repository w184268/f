# WatchTube

> 孩子在电话手表上问一句："恐龙为什么灭绝了？"
> 微信/微聊里自动收到一段讲恐龙的科普小视频。家长全程不用插手。

一套把**手表提问**和**视频网站**接起来的自动化管道：

```
孩子在手表上说/打一句话
        │  小天才 App 弹出通知
        ▼
手机上的 WatchBridge（本项目 App）
        │  HTTP 转发
        ▼
WatchTube 服务端
   ├─ 理解这句话要不要找视频（大模型 / 本地规则）
   ├─ B站检索 + 儿童安全过滤 + 打分选片
   ├─ 下载 → 剪前 60 秒 → 压成 320×320 / ~3MB
   └─ 家长审核（可选）
        │  手机取件
        ▼
视频写进手机相册 → 自动/一键发回微聊
        │
        ▼
孩子抬手腕就看到了 🎉
```

---

## 一、开始前请先看这段（重要）

这套东西依赖三件外部事实，动手前心里有数：

| 事实 | 说明 | 应对 |
|---|---|---|
| 小天才没有开放 API | 官方不提供消息接口 | 走 Android 通知监听 + 无障碍，纯本地方案 |
| 微聊支持收发视频 | App 隐私政策确认微聊可传文本/语音/图片/视频 | ✅ 主通道可行 |
| **部分旧型号不支持收文本/视频** | 如 Y01 收不到文本微聊 | 已内置降级：视频 → 语音 → 纯文字 |

另外请注意：**压制后的视频请在家庭内使用**，不要二次分发，尊重 UP 主的劳动。

---

## 二之一、我该从哪儿开始？

| 你想做什么 | 看这里 |
|---|---|
| 完全照着点、不想看命令行 | **[使用指南.html](使用指南.html)**（浏览器打开，含 APK 三种获取路线） |
| 先确认自己电脑环境够不够 | `python3 tools/check_env.py` |
| 不接手机，先验证服务端好不好使 | `python3 tools/selftest.py "恐龙为什么会灭绝啊"` |
| 想拿 APK | 见「APK 从哪来」一节 |
| 想改代码/了解原理 | 往下读 |

> APK 说明：仓库里只有源码，没有编译好的 apk —— Android 应用必须编译后才能安装。
> 仓库已内置 GitHub Actions 工作流 `.github/workflows/build-apk.yml`，把项目传到 GitHub
> 就会自动帮你编出 `app-debug.apk`，全程网页操作，**本地不用装任何开发工具**。

---

## 二、服务端：30 分钟搭起来

### 1. 装依赖

服务端只依赖 Python 3.10+ 和 ffmpeg。

```bash
cd watchtube/server

# ffmpeg
#   macOS:   brew install ffmpeg
#   Ubuntu:  sudo apt install ffmpeg
#   Windows: 到 gyan.dev 下载静态包，把 ffmpeg.exe 放进 PATH

python3 -m pip install -r requirements.txt
```

> **关于 ffmpeg 编码器**：不同发行版差异很大。有的机器没有 `libx264`（GPL 版才有），
> 本项目的 `media.py` 会自动探测并按 `libx264 → libopenh264 → h264_qsv/nvenc → mpeg4` 降级，
> 所以即使是个残缺版 ffmpeg 也能跑，只是画质稍逊。

### 2. 改配置

```bash
cp config.example.yaml config.yaml
```

最少要改三处：

```yaml
server:
  public_base_url: "http://192.168.1.10:8787"   # 手机能访问到的地址，非常重要
  bridge_key: "自己起一串长密码"                  # 手机 App 里要填一样的

understanding:
  api_key: "sk-xxxx"                            # 留空则自动用本地规则引擎，也能跑
```

> **大模型推荐用国内便宜的**：DeepSeek / 通义千问都行，任何 OpenAI 兼容接口都能接。
> 一条消息几分钱，主要作用是理解孩子含糊的表达（"那个大恐龙叫啥"），把它变成能搜出东西的关键词。
> **没有 Key 也能用**：自动降级到规则引擎，效果差一点但完全可用。

### 3. 先不用手机，自测一遍

```bash
python3 ../tools/selftest.py "恐龙为什么会灭绝啊妈妈"
```

正常会看到：

```
✅ 意图识别  is_request=True query='恐龙为什么会灭绝啊'
✅ 检索 → 选片  《儿童睡前故事《恐龙是怎么灭绝的》》 by 暖暖儿童睡前故事
✅ 压制成片  2117KB / 45s
✅ 成片可播放  320x180 h264 2117KB
🎉 全链路跑通
```

### 4. 启动

```bash
./run.sh          # Linux / macOS
run.bat           # Windows
# 或：python3 main.py --port 8787
```

打开浏览器 `http://本机IP:8787` 就是**家长看板**：可以看到孩子问了什么、选中了哪条视频、
在线预览、同意/拒绝下发、查看处理日志。

### 5. 让手机在户外也能访问（二选一）

| 方案 | 做法 | 适合 |
|---|---|---|
| **Tailscale**（推荐） | 手机和家里电脑都装，组成虚拟局域网 | 长期稳定、不用公网 IP |
| Cloudflare Tunnel | `cloudflared tunnel --url http://localhost:8787` | 临时试用，免配置 |

把得到的地址填回 `config.yaml` 的 `public_base_url`，重启服务端。

---

## 三、手机端：装 App

### 方案 A：编译本项目 App（推荐）

1. 用 Android Studio 打开 `android/` 目录（建议 Koala 2024.1 或更新）
2. 第一次会自动下载 Gradle，耐心等几分钟
3. 手机打开 USB 调试，点 ▶️ 直接装到手机上

App 装好后，按顺序做四件事：

| # | 要做的事 | 在哪 |
|---|---|---|
| 1 | 填服务端地址和 bridge_key | App 首页两个输入框 → 保存 |
| 2 | 开启**通知使用权** | 点"开启消息监听权限"按钮，勾选 WatchBridge |
| 3 | 加入**电池优化白名单** | 点对应按钮，选"允许" |
| 4 | 想要全自动的话，开启**无障碍服务** | 点"开启无障碍"按钮，找到「手表视频投递」打开 |

**第一次务必开着"干跑模式"**：它会把小天才界面上所有按钮 dump 到 App 日志里，
但不真的点击。对着日志把发送脚本调准，再关掉干跑。脚本在 App 里可编辑（`Prefs.sendSteps` 默认值，
后续版本会做成可视化编辑器）。

> 厂商后台限制各不相同，如果隔天发现不工作了，基本都是被省电策略杀了。
> 华为/荣耀：应用启动管理 → 手动管理，允许后台活动 + 自启动 + 关联启动
> 小米：安全中心 → 自启动管理 + 省电策略选"无限制"
> OPPO/realme：允许后台高耗电 + 关闭"智能冻结"
> vivo：i管家 → 后台高耗电允许

### 方案 B：不想编译 → Tasker（免代码，少一步自动化）

1. Tasker 建 Profile：Event → Plugin → AutoNotification → Intercept，Apps 选 `com.xtc.watch`
2. Task 里用 HTTP Request 把 `%antitle %antext` POST 到 `http://服务端/api/v1/ingest`
   （Body 用 `{"sender":"%antitle","text":"%antext","device_id":"tasker"}`，Header 带 `X-Api-Key`）
3. 再用一个定时 Task 每 2 分钟 GET `/api/v1/outbox`，有视频就下载到相册
4. **最后一步手动发**：打开小天才 App → 微聊 → 发送刚下载的视频

方案 B 省掉编译，代价是最后一哆嗦要手动点几下。

---

## 四、参数怎么调

都在 `server/config.yaml`：

| 参数 | 默认 | 什么时候改 |
|---|---|---|
| `video.max_duration` | 60 秒 | 存储吃紧改 30–45；想让孩子看完整些改到 90（约 4MB） |
| `video.width` | 320 | 屏幕大的旗舰款可提到 480 |
| `video.max_size_mb` | 8 | 超过会自动降码率重压，一般不用动 |
| `delivery.reply_mode` | video | 旧型号改用 `audio` 或 `text` |
| `delivery.daily_quota` | 20 | 怕孩子刷视频才调低 |
| `moderation.mode` | auto | 已关闭审批、全自动下发。想恢复人工把关就改成 `review` |
| `search.min_play` | 3000 | 觉得内容太杂就调高，想啥都能搜到就调低 |
| `search.blocked_keywords` | 见文件 | 遇到不想要的内容类型，往里加词 |

### 全自动模式下，靠什么保证内容安全

关掉人工审批之后，**安全过滤一步都没少**，只是全部由机器完成、不打断自动化流程：

- 黑名单词一票否决（成人/恐怖/擦边/灵异等）
- 标题相关性校验，牛头不对马嘴的结果直接丢弃
- 播放量门槛过滤营销号低质视频
- 优先给"科普/动物/亲子/纪录片"这类分区加权
- 每日条数上限 + 同一句话短时去重，防止被刷屏

这五层都是**自动生效**的，不会阻塞下发。真想人工把关时，
把 `moderation.mode` 改回 `review`，视频会先停在看板等你点"同意下发"。

孩子问到"死亡""吵架"这类话题时，如果你希望它转人工而不是自动回复，
往 `always_review_keywords` 里把词填回去即可（当前已清空）。

---

## 五、三种回传方式怎么选

| 方式 | 兼容型号 | 体验 | 配置 |
|---|---|---|---|
| **video** | 支持微聊视频的型号 | 最好，孩子直接看 | `reply_mode: video` |
| **audio** | 几乎所有型号 | 较好，像家长语音讲解 | `reply_mode: audio` + `pip install edge-tts` |
| **text** | 全部 | 只有一句摘要 | `reply_mode: text` |
| **auto** | 自动尝试 video，失败降级 audio | 最省心 | `reply_mode: auto` |

> 先确认你的手表能不能收视频：用小天才 App 手动发一个视频给孩子试试。
> 能收到就走 video；发不出去就改 `audio`。

---

## 六、出问题怎么查

| 现象 | 原因 | 确认办法 |
|---|---|---|
| 孩子发消息，服务端没反应 | 通知监听没授权 / 被去重 | App 日志看有没有"捕获消息"；同一条内容 8 秒内的重复会被忽略 |
| 一直"取件失败" | `public_base_url` 填错，或手机不在同一网络 | 手机浏览器直接打开这个地址试试 |
| "成片可播放"失败 | ffmpeg 缺编码器 | `ffmpeg -encoders \| grep 264` |
| B站取流失败 | IP 被风控或 wbi 密钥过期 | 看服务端日志；密钥每 24 小时自动刷新，也可用环境变量 `WT_SEARCH__` 调参数 |
| 自动发送点了乱七八糟的地方 | 发送脚本的按钮文案对不上 | 开干跑模式，看 dump 出来的节点树，改 `sendSteps` |
| 整晚没工作 | 被省电策略杀 | 按第三节各厂商设置加白名单；再用 `adb shell dumpsys battery` 确认 |

服务端每个任务都有完整事件日志，在家长看板点"日志"就能看到全过程。

---

## 七、目录结构

```
watchtube/
├── server/                  # 大脑：理解、检索、压片、API
│   ├── wt/
│   │   ├── brain.py         #   大模型意图识别 + 规则引擎降级
│   │   ├── bili.py          #   B站搜索 / wbi 签名 / 取流
│   │   ├── safety.py        #   儿童安全过滤与打分
│   │   ├── media.py         #   下载 + ffmpeg 压制（编码器自动降级）
│   │   ├── pipeline.py      #   全流程编排
│   │   ├── store.py         #   SQLite 状态机
│   │   └── api.py           #   HTTP 接口 + 家长看板
│   ├── web/index.html       # 家长看板页面
│   ├── main.py
│   └── config.example.yaml
├── android/                 # 手和脚：收消息 + 回传
│   └── app/src/main/java/com/kid/watchbridge/
│       ├── ChatListener.kt  #   通知监听，抓微聊消息
│       ├── BridgeService.kt #   常驻轮询、下载、写相册
│       ├── SendService.kt   #   无障碍自动发送（支持干跑）
│       └── MainActivity.kt  #   配置与日志界面
├── tools/selftest.py        # 不需要手机的自测脚本
└── README.md
```

接口一览（手机端用的就这三个）：

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/v1/ingest` | 孩子的新消息 |
| GET | `/api/v1/outbox` | 拉待下发的内容 |
| POST | `/api/v1/ack` | 回报送达/失败 |

---

## 八、最后

这套东西的价值在于：**孩子的好奇心往往只有 30 秒窗口**。等放学回家再问
"你早上想问什么来着"，那个劲儿早就过去了。

要魔改也很容易 —— 换成 YouTube Kids、换成给孩子讲睡前故事的视频源，
只需要替换 `search` 那一层，其余部分不用动。
