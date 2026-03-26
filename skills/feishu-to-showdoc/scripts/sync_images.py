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


def download_from_feishu(token, mcp_config, tmp_dir):
    """从飞书 MCP 下载图片，返回本地文件路径"""
    payload = json.dumps({
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": "fetch-file",
            "arguments": {"resource_token": token, "type": "media"},
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


def convert_lark_table(match):
    """将 <lark-table> 转换为 Markdown 表格"""
    table_html = match.group(0)
    rows = re.findall(r'<lark-tr>(.*?)</lark-tr>', table_html, re.DOTALL)
    if not rows:
        return ""

    md_rows = []
    for row in rows:
        cells = re.findall(r'<lark-td>(.*?)</lark-td>', row, re.DOTALL)
        # 清理单元格内容：去除多余空白、属性标注、换行
        cleaned = []
        for cell in cells:
            text = cell.strip()
            # 去除 {align="center"} 等属性
            text = re.sub(r'\s*\{[^}]*\}\s*', '', text)
            # 合并多行为单行
            text = re.sub(r'\s*\n\s*', ' ', text)
            text = text.strip()
            cleaned.append(text)
        md_rows.append(cleaned)

    if not md_rows:
        return ""

    # 第一行作为表头
    col_count = max(len(r) for r in md_rows)
    lines = []
    header = md_rows[0]
    # 补齐列数
    header += [''] * (col_count - len(header))
    lines.append('| ' + ' | '.join(header) + ' |')
    lines.append('| ' + ' | '.join(['---'] * col_count) + ' |')
    for row in md_rows[1:]:
        row += [''] * (col_count - len(row))
        lines.append('| ' + ' | '.join(row) + ' |')

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

    # 提取图片 token
    tokens = extract_image_tokens(content)
    print(f"共 {len(tokens)} 张图片需要处理")

    if not tokens:
        # 无图片，直接转换 Markdown
        result = convert_markdown(content, {})
        with open(args.output, "w") as f:
            f.write(result)
        print("无图片，仅转换 Markdown 完成")
        return

    # 创建临时目录
    tmp_dir = "/tmp/feishu_showdoc_images"
    os.makedirs(tmp_dir, exist_ok=True)

    # 逐张处理图片
    mapping = {}
    failed = []

    for i, token in enumerate(tokens):
        print(f"[{i+1}/{len(tokens)}] {token}", end=" ")

        # 下载
        local_path = None
        for attempt in range(2):  # 最多重试一次
            try:
                local_path = download_from_feishu(token, mcp_config, tmp_dir)
                if local_path:
                    break
            except Exception as e:
                if attempt == 0:
                    print(f"下载重试...", end=" ")

        if not local_path:
            print("下载失败")
            failed.append(token)
            continue

        size = os.path.getsize(local_path)

        # 上传
        url = None
        for attempt in range(2):
            try:
                url = upload_to_showdoc(local_path, creds)
                if url:
                    break
            except Exception as e:
                if attempt == 0:
                    print(f"上传重试...", end=" ")

        if url:
            mapping[token] = url
            print(f"OK ({size} bytes)")
        else:
            print("上传失败")
            failed.append(token)

    # 转换 Markdown
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
