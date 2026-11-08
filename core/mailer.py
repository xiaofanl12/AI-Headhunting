"""邮件触达：SMTP 发送 + 失败指数退避重试 + 状态落库。"""
import re
import smtplib
import time
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

from config import SMTP_FROM_NAME, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT, SMTP_USER


def _smtp_send(to: str, subject: str, body: str):
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((SMTP_FROM_NAME, SMTP_USER))
    msg["To"] = formataddr((to.split("@")[0], to))
    if SMTP_PORT == 465:
        server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=10)
    else:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10)
        server.starttls()
    try:
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, [to], msg.as_string())
    finally:
        server.quit()


def send_with_retry(to: str, subject: str, body: str, max_retries: int = 3) -> bool:
    """指数退避重试（2s/4s/8s），最终成功/失败由调用方落库。"""
    for attempt in range(max_retries):
        try:
            _smtp_send(to, subject, body)
            return True
        except Exception as e:
            print(f"[mailer] 第 {attempt + 1} 次发送失败: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** (attempt + 1))
    return False


_META_TAIL = re.compile(
    r"[（(][^（()）]{0,80}?"
    r"(字数|约\d+字|未提及|仅以|以上|正文|不含内部|算法评估|说明[:：]|已按要求|请替换|以下为)"
    r"[^（()）]{0,80}?[）)]\s*$"
)


def tidy_body(body: str) -> str:
    """整理邮件版式：LLM 常把段落挤成一行，这里按「您好 / 感谢 / 请回复 / 落款」补空行。"""
    text = body.replace("\r\n", "\n").strip()
    if "\n" in text.replace("\n\n", "\n").strip():
        return text  # 已经有换行结构，尊重原文
    # 单段文本：在常见段落边界插入空行
    text = re.sub(r"[。！!]\s*(?=感谢|我们|诚挚|期待|请回复|祝)", "\n\n", text, count=2)
    text = re.sub(r"[。！!]\s*(?=[\u4e00-\u9fa5A-Za-z0-9]{2,12}\s*招聘团队)", "\n\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def strip_llm_meta(body: str) -> str:
    """剥掉 LLM 爱在邮件末尾附的「自我说明」。

    实测 GLM 会写「（正文约115字，未提及内部算法评估细节，仅以简历契合度作为邀约依据）」
    这类元注释——发给候选人等于把内部评估逻辑泄露出去，必须清掉。
    """
    lines = [ln for ln in body.splitlines()]
    # 从末尾往前削，最多削 3 行（正常邮件末尾是签名，不该被误删）
    for _ in range(3):
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:
            break
        last = lines[-1].strip()
        if _META_TAIL.search(last) or (last.startswith(("（", "(")) and last.endswith(("）", ")"))):
            lines.pop()
        else:
            break
    return "\n".join(lines).strip()


def draft_email(candidate: dict, position: dict, company: dict, llm_text: str | None) -> dict:
    """生成邀约邮件草稿：LLM 可用则生成，否则规则模板。"""
    subject = f"【面试邀约】{position['title']} - {candidate['name']}"
    if llm_text:
        body = llm_text.strip()
        # LLM 有时会在正文里重复写"主题：xxx"（可能带 Markdown 加粗/标题符号），剥离首行
        lines = body.splitlines()
        while lines and re.sub(r"[\s*#>\-]", "", lines[0]).startswith("主题"):
            lines.pop(0)
        body = strip_llm_meta("\n".join(lines).lstrip())
        if body:
            return {"subject": subject, "body": tidy_body(body)}
        # 清洗后为空说明 LLM 只吐了元说明，退回规则模板
    body = (
        f"{candidate['name']} 您好：\n\n"
        f"我们在「{company.get('name', position.get('company_id'))}」看到您与"
        f"「{position['title']}」职位高度匹配（技能：{', '.join(position.get('skill_tags') or [])}），"
        f"诚挚邀请您参加面试。请回复本邮件确认方便的时间。\n\n"
        f"祝好！\n{SMTP_FROM_NAME}"
    )
    return {"subject": subject, "body": body}
