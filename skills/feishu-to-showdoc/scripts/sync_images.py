#!/usr/bin/env python3
"""
飞书文档图片同步到 ShowDoc

功能：
1. 从飞书 Markdown 中提取图片 token
2. 通过飞书 MCP HTTP 接口下载图片
3. 上传到 ShowDoc
4. 转换飞书 Markdown 为标准 Markdown，替换图片 URL
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys


def load_feishu_mcp_config():
    """从 ~/.claude.json 读取飞书 MCP 配置"""
    config_path = os.path.expanduser("~/.claude.json")
    if not os.path.exists(config_path):
        print("错误：找不到 ~/.claude.json", file=sys.stderr)
        sys.exit(1)

    with open(config_path) as f:
        config = json.load(f)

    for name, server in config.get("mcpServers", {}).items():
        if "feishu" in name.lower() or "Feishu" in name:
            if server.get("type") == "http":
                return {
                    "url": server["url"],
                    "api_key": server.get("headers", {}).get("X-API-Key", ""),
                }
    print("错误：在 ~/.claude.json 中找不到飞书 MCP 配置", file=sys.stderr)
    sys.exit(1)


def load_credentials(cred_path, project_name):
    """加载 ShowDoc 认证信息"""
    if not os.path.exists(cred_path):
        print(f"错误：找不到认证文件 {cred_path}", file=sys.stderr)
        sys.exit(1)

    with open(cred_path) as f:
        creds = json.load(f)

    project = creds.get("projects", {}).get(project_name)
    if not project:
        print(f"错误：找不到项目 '{project_name}' 的凭据", file=sys.stderr)
        sys.exit(1)

    return {
        "base_url": creds["showdoc_base_url"],
        "user_token": creds["showdoc_user_token"],
        "api_key": project["api_key"],
        "api_token": project["api_token"],
    }


def extract_image_tokens(content):
    """提取飞书 Markdown 中的图片 token（保持顺序，去重）"""
    tokens = re.findall(r'<image token="([^"]+)"', content)
    return list(dict.fromkeys(tokens))


def extract_whiteboard_tokens(content):
    """提取飞书 Markdown 中的画板 token（保持顺序，去重）"""
    tokens = re.findall(r'<whiteboard token="([^"]+)"', content)
    return list(dict.fromkeys(tokens))


def download_from_feishu(token, mcp_config, tmp_dir, resource_type="media"):
    """从飞书 MCP 下载图片或画板，返回本地文件路径"""
    payload = json.dumps({
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": "fetch-file",
            "arguments": {"resource_token": token, "type": resource_type},
        },
        "id": 1,
    })

    result = subprocess.run(
        [
            "curl", "-s", "-X", "POST", mcp_config["url"],
            "-H", "Content-Type: application/json",
            "-H", f"X-API-Key: {mcp_config['api_key']}",
            "-d", payload,
            "--max-time", "90",
        ],
        capture_output=True, text=True, timeout=120,
    )

    resp = json.loads(result.stdout)
    for item in resp.get("result", {}).get("content", []):
        if item.get("type") == "image":
            b64_data = item["data"]
            mime = item.get("mimeType", "image/png")
            ext = ".png" if "png" in mime else ".jpg"
            path = os.path.join(tmp_dir, f"{token}{ext}")
            with open(path, "wb") as f:
                f.write(base64.b64decode(b64_data))
            return path

    return None


def upload_to_showdoc(file_path, creds):
    """上传图片到 ShowDoc，返回图片 URL"""
    upload_url = f"{creds['base_url']}/server/index.php?s=/api/attachment/uploadImg"
    result = subprocess.run(
        [
            "curl", "-s", "-X", "POST", upload_url,
            "-F", f"editormd-image-file=@{file_path}",
            "-F", f"user_token={creds['user_token']}",
            "-F", "item_id=0",
            "-F", "page_id=0",
            "--max-time", "30",
        ],
        capture_output=True, text=True, timeout=60,
    )

    resp = json.loads(result.stdout)
    if resp.get("success") == 1:
        # 将 http 转为 https
        url = resp["url"]
        if creds["base_url"].startswith("https://"):
            url = url.replace("http://", "https://")
        return url
    return None


def _clean_cell(text):
    """清理单元格内容"""
    text = text.strip()
    # 去除 {align="center"} 等属性
    text = re.sub(r'\s*\{[^}]*\}\s*', '', text)
    # 合并多行为单行（用空格连接）
    text = re.sub(r'\s*\n\s*', ' ', text)
    # 管道符会破坏 Markdown 表格，转义
    text = text.replace('|', '\\|')
    return text.strip()


def convert_lark_table(match):
    """将 <lark-table> 转换为 Markdown 表格，支持 rowspan/colspan"""
    table_html = match.group(0)

    # 获取列数（优先从 cols 属性读取）
    cols_match = re.search(r'cols="(\d+)"', table_html)
    declared_cols = int(cols_match.group(1)) if cols_match else 0

    rows = re.findall(r'<lark-tr>(.*?)</lark-tr>', table_html, re.DOTALL)
    if not rows:
        return ""

    # 构建二维网格，处理 rowspan 和 colspan
    grid = []  # grid[row][col] = cell_text
    for row_idx, row_html in enumerate(rows):
        # 确保当前行存在
        while len(grid) <= row_idx:
            grid.append({})

        # 提取所有 td（保留属性）
        tds = re.findall(r'<lark-td([^>]*)>(.*?)</lark-td>', row_html, re.DOTALL)

        col_idx = 0
        for td_attrs, td_content in tds:
            # 跳过已被 rowspan 占据的位置
            while col_idx in grid[row_idx]:
                col_idx += 1

            text = _clean_cell(td_content)

            # 解析 rowspan/colspan
            rs_match = re.search(r'rowspan="(\d+)"', td_attrs)
            cs_match = re.search(r'colspan="(\d+)"', td_attrs)
            rowspan = int(rs_match.group(1)) if rs_match else 1
            colspan = int(cs_match.group(1)) if cs_match else 1

            # 填充当前格和合并区域
            for r in range(rowspan):
                for c in range(colspan):
                    target_row = row_idx + r
                    target_col = col_idx + c
                    while len(grid) <= target_row:
                        grid.append({})
                    if r == 0 and c == 0:
                        grid[target_row][target_col] = text
                    else:
                        grid[target_row][target_col] = ""

            col_idx += colspan

    if not grid:
        return ""

    # 确定总列数
    col_count = declared_cols or max((max(r.keys()) + 1 if r else 0) for r in grid)

    # 转为列表
    lines = []
    for row_idx, row_dict in enumerate(grid):
        cells = [row_dict.get(c, "") for c in range(col_count)]
        lines.append('| ' + ' | '.join(cells) + ' |')
        if row_idx == 0:
            lines.append('| ' + ' | '.join(['---'] * col_count) + ' |')

    return '\n'.join(lines)


def convert_markdown(content, image_mapping):
    """将飞书 Markdown 转换为标准 Markdown"""

    # 替换图片标签
    def replace_image(match):
        token = match.group(1)
        url = image_mapping.get(token, "")
        if url:
            return f"![image]({url})"
        return "[图片：同步失败]"

    content = re.sub(r'<image token="([^"]+)"[^/]*/>', replace_image, content)

    # 替换画板标签（复用同一个 image_mapping）
    def replace_whiteboard(match):
        token = match.group(1)
        url = image_mapping.get(token, "")
        if url:
            return f"![image]({url})"
        return "[画板：同步失败]"

    content = re.sub(r'<whiteboard token="([^"]+)"[^/]*/>', replace_whiteboard, content)

    # 转换 lark-table 为 Markdown 表格（必须在移除其他标签之前）
    content = re.sub(r'<lark-table[^>]*>.*?</lark-table>', convert_lark_table, content, flags=re.DOTALL)

    # 移除 grid/column 标签
    content = re.sub(r"<grid[^>]*>\s*", "", content)
    content = re.sub(r"</grid>\s*", "", content)
    content = re.sub(r"<column[^>]*>\s*", "", content)
    content = re.sub(r"</column>\s*", "", content)

    # 转换 callout 标签为 blockquote
    content = re.sub(r'<callout[^>]*>\s*', '> ', content)
    content = re.sub(r'\s*</callout>', '', content)

    # 转换 quote-container 为 blockquote
    content = re.sub(r"<quote-container>\s*", "> ", content)
    content = re.sub(r"\s*</quote-container>", "", content)

    # 转换 sheet 标签
    content = re.sub(r'<sheet token="[^"]*"/>', "[表格：请从飞书原文档获取]", content)

    # 转换 text bgcolor/color 标签
    content = re.sub(r'<text[^>]*>(.*?)</text>', r"\1", content)

    # 去除代码块语言标识后的 {wrap} 修饰符（飞书特有，ShowDoc 不支持）
    content = re.sub(r'```(\w+)\s*\{wrap\}', r'```\1', content)

    # 去掉代码块外 4+ 前导空格的段落（飞书缩进会被 Markdown 渲染为代码块）
    lines = content.split('\n')
    cleaned = []
    in_code = False
    for line in lines:
        if line.strip().startswith('```'):
            in_code = not in_code
            cleaned.append(line)
            continue
        if not in_code:
            stripped = line.lstrip()
            leading = len(line) - len(stripped)
            if leading >= 4 and stripped and not stripped.startswith(('#', '|', '-', '*', '>')):
                line = stripped
        cleaned.append(line)
    content = '\n'.join(cleaned)

    # 列表项后紧跟代码围栏时插入空行，否则围栏不被识别
    content = re.sub(r'^(- .+)\n(```)', r'\1\n\n\2', content, flags=re.MULTILINE)

    # 清理多余空行
    content = re.sub(r"\n{4,}", "\n\n\n", content)

    return content


def main():
    parser = argparse.ArgumentParser(description="飞书文档图片同步到 ShowDoc")
    parser.add_argument("--input", required=True, help="飞书原始 Markdown 文件路径")
    parser.add_argument("--credentials", required=True, help="credentials.json 路径")
    parser.add_argument("--project", required=True, help="项目标识（credentials.json 中的 key）")
    parser.add_argument("--output", required=True, help="输出 Markdown 文件路径")
    args = parser.parse_args()

    # 加载配置
    mcp_config = load_feishu_mcp_config()
    creds = load_credentials(args.credentials, args.project)

    with open(args.input, "r") as f:
        content = f.read()

    # 提取图片和画板 token
    image_tokens = extract_image_tokens(content)
    whiteboard_tokens = extract_whiteboard_tokens(content)
    print(f"共 {len(image_tokens)} 张图片、{len(whiteboard_tokens)} 个画板需要处理")

    # 创建临时目录
    tmp_dir = "/tmp/feishu_showdoc_images"
    os.makedirs(tmp_dir, exist_ok=True)

    # 统一的下载上传流程
    mapping = {}
    failed = []

    def process_token(token, index, total, resource_type="media", label="图片"):
        print(f"[{index}/{total}] {label} {token}", end=" ")
        local_path = None
        for attempt in range(2):
            try:
                local_path = download_from_feishu(token, mcp_config, tmp_dir, resource_type)
                if local_path:
                    break
            except Exception:
                if attempt == 0:
                    print("下载重试...", end=" ")
        if not local_path:
            print("下载失败")
            failed.append(token)
            return
        size = os.path.getsize(local_path)
        url = None
        for attempt in range(2):
            try:
                url = upload_to_showdoc(local_path, creds)
                if url:
                    break
            except Exception:
                if attempt == 0:
                    print("上传重试...", end=" ")
        if url:
            mapping[token] = url
            print(f"OK ({size} bytes)")
        else:
            print("上传失败")
            failed.append(token)

    total = len(image_tokens) + len(whiteboard_tokens)
    idx = 0
    for token in image_tokens:
        idx += 1
        process_token(token, idx, total, "media", "图片")
    for token in whiteboard_tokens:
        idx += 1
        process_token(token, idx, total, "whiteboard", "画板")

    # 转换 Markdown（image_mapping 同时包含图片和画板的 URL 映射）
    result = convert_markdown(content, mapping)
    with open(args.output, "w") as f:
        f.write(result)

    # 保存映射（便于调试）
    mapping_path = os.path.join(tmp_dir, "mapping.json")
    with open(mapping_path, "w") as f:
        json.dump(mapping, f, indent=2)

    print(f"\n完成: {len(mapping)} 成功, {len(failed)} 失败")
    if failed:
        print(f"失败的 token: {failed}")
    print(f"输出文件: {args.output}")


if __name__ == "__main__":
    main()
