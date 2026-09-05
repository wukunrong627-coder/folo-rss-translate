# Folo RSS 中文翻译

使用 RSSHub + Argos 离线模型，为美联社热门新闻、Yahoo 国际新闻生成中文标题和中英对照内容。不需要 API Key 或 Folo 密码。翻译范围是 RSS 提供的正文/摘要，不额外抓取全文。

## 在 Folo 中订阅

首次 GitHub Actions 成功后，在 Folo 点击添加订阅，分别粘贴：

- 美联社：`https://raw.githubusercontent.com/wukunrong627-coder/folo-rss-translate/main/public/ap.xml`
- Yahoo：`https://raw.githubusercontent.com/wukunrong627-coder/folo-rss-translate/main/public/yahoo.xml`

查看 [运行记录](https://github.com/wukunrong627-coder/folo-rss-translate/actions) 和 [更新时间](public/status.json)。首次运行成功前，上述链接不可用。

## 自动更新

每小时第 17、47 分钟计划运行。也可在 Actions → Translate and publish RSS → Run workflow 手动启动。任何来源失败则停止本轮发布，保留此前公开内容。模型及翻译缓存不上传到仓库。

GitHub 调度可能延迟；Folo 另有抓取间隔。公开仓库长时间无活动时计划任务可能被停用，请到 Actions 检查。机器翻译可能有误，正文保留原文及来源链接。

## 本地运行

需要 Python 3.12/3.13 及运行在 1200 端口的 RSSHub。

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python download_model.py
$env:MODEL_DIR = "$PWD\models"
$env:DATA_DIR = "$PWD\data"
$env:RSSHUB_BASE_URL = 'http://127.0.0.1:1200'
.\.venv\Scripts\python app.py
```

预览 http://127.0.0.1:1201 。Folo 云端无法抓取电脑的 localhost，应使用公网链接。

单次生成运行 `python generate.py`；测试运行 `python -m unittest -v test_app.py`。调整 sources.json 可修改来源和条目数。

模型来源：https://github.com/argosopentech/argospm-index  
RSSHub：https://github.com/DIYgod/RSSHub
