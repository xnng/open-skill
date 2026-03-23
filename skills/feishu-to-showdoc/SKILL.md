---
name: feishu-to-showdoc
description: '将飞书文档（含图片）同步到 ShowDoc。当用户说「同步到 ShowDoc」「飞书同步 ShowDoc」「把飞书文档写到 ShowDoc」「同步文档到 docs.xasre.com」或提供飞书链接并提及 ShowDoc 时触发。'
---

# 飞书文档同步到 ShowDoc

将飞书云文档的内容（含图片）同步到 ShowDoc 文档平台。

## 前置条件

同步需要两组认证信息和一个目标目录，存储在 `~/.claude/skills/origin/feishu-to-showdoc/credentials.json` 中。

### credentials.json 结构

```json
{
  "showdoc_base_url": "https://docs.xasre.com",
  "showdoc_user_token": "<用于图片上传的用户认证 token>",
  "projects": {
    "<项目标识>": {
      "api_key": "<ShowDoc Open API Key>",
      "api_token": "<ShowDoc Open API Token>"
    }
  }
}
```

### 首次使用交互流程

如果 `credentials.json` 不存在或缺少必要信息，按以下顺序向用户收集：

1. **ShowDoc 认证**（仅首次）：
   - `showdoc_base_url`：ShowDoc 站点地址（如 `https://docs.xasre.com`）
   - `showdoc_user_token`：用于图片上传的认证 token。获取方式：用浏览器登录 ShowDoc → F12 打开开发者工具 → Console 输入 `JSON.parse(localStorage.getItem('userinfo')).user_token` → 复制返回值

2. **项目 API 凭据**（每个 ShowDoc 项目/知识库独立）：
   - 提示用户：进入目标 ShowDoc 项目 → 点击右上角「...」菜单 → 点击「开放 API」 → 复制 API Key 和 API Token
   - 用一个用户自定义的项目标识作为 key 存储（如「操作手册」）

3. **同步目标目录**：
   - 询问用户要将文档存放在哪个目录下（对应 ShowDoc 的 `cat_name` 参数）
   - 支持多级目录，用 `/` 分隔（如 `智慧能源SAAS系统/运维端`）

**所有信息确认完毕后才能开始同步。** 将凭据写入 credentials.json，后续使用时直接读取。

## 同步流程

### 第一步：获取飞书文档

使用飞书 MCP 工具获取文档内容：

```
工具: mcp__Feishu-mcp-sre__fetch-doc
参数: doc_id = <飞书文档 URL 或 ID>
```

### 第二步：转换 Markdown

飞书返回的 Markdown 包含特有标签，需要转换为标准 Markdown：

| 飞书标签 | 转换规则 |
|---------|---------|
| `<image token="xxx" .../>` | 替换为 ShowDoc 图片 URL（见第三步） |
| `<quote-container>...</quote-container>` | 转换为 blockquote `> ` |
| `<grid>`, `<column>` | 直接移除标签，保留内容 |
| `<sheet token="xxx"/>` | 替换为 `[表格：请从飞书原文档获取]` |
| `<text bgcolor="xxx">content</text>` | 保留 content，移除标签 |

### 第三步：同步图片

这是关键步骤。飞书图片使用内部 token，无法直接在外部访问，必须下载后重新上传到 ShowDoc。

使用 `scripts/sync_images.py` 脚本批量处理：

```bash
python3 ~/.claude/skills/origin/feishu-to-showdoc/scripts/sync_images.py \
  --input <飞书原始 markdown 文件路径> \
  --credentials ~/.claude/skills/origin/feishu-to-showdoc/credentials.json \
  --project <项目标识> \
  --output <输出 markdown 文件路径>
```

脚本会：
1. 从 Markdown 中提取所有 `<image token="xxx">` 的 token
2. 通过飞书 MCP HTTP 接口下载每张图片（base64）
3. 上传到 ShowDoc（通过 `uploadImg` API）
4. 将 `<image>` 标签替换为 `![image](ShowDoc URL)`
5. 同时完成所有其他 Markdown 标签的转换
6. 输出最终的标准 Markdown 文件

脚本运行前需要先确认飞书 MCP 配置。从 `~/.claude.json` 中读取 Feishu MCP 服务器的 URL 和 API Key：

```python
# 示例：从 ~/.claude.json 提取
import json
config = json.load(open(os.path.expanduser('~/.claude.json')))
for name, server in config.get('mcpServers', {}).items():
    if 'feishu' in name.lower() or 'Feishu' in name:
        mcp_url = server['url']  # 如 https://mcp.xasre.com/feishu/feishu-mcp/mcp
        mcp_api_key = server['headers']['X-API-Key']
```

如果找不到飞书 MCP 配置，提示用户检查 `~/.claude.json` 中的 mcpServers 配置。

### 第四步：推送到 ShowDoc

使用 ShowDoc Open API 创建或更新文档：

```bash
curl -s -X POST '<showdoc_base_url>/server/index.php?s=/api/open/updatePage' \
  -d "api_key=<api_key>" \
  -d "api_token=<api_token>" \
  -d "cat_name=<目标目录>" \
  --data-urlencode "page_title=<文档标题>" \
  --data-urlencode "page_content@<转换后的 markdown 文件>" \
  -d "s_number=1"
```

- `page_title`：使用飞书文档的标题
- `cat_name`：用户指定的目标目录
- `updatePage` 接口会按标题匹配：已存在则更新，不存在则创建

### 第五步：验证

同步完成后，输出 ShowDoc 文档的访问地址，格式为：
`<showdoc_base_url>/web/#/<item_id>/<page_id>`

其中 `item_id` 和 `page_id` 从 API 响应中获取。

## 注意事项

- 飞书文档中嵌入的电子表格（`<sheet>` 标签）无法自动迁移，会保留占位提示
- 单次同步的图片数量没有限制，脚本会逐张处理，个别失败会自动重试一次
- ShowDoc 的 `updatePage` 会对内容做 `htmlspecialchars` 处理，这是 ShowDoc 的内置行为，不影响最终渲染
