import crypto from "node:crypto";

function buildSignedWebhook(webhook: string, secret: string): string {
  if (!secret) {
    return webhook;
  }
  const timestamp = Date.now().toString();
  const signature = crypto
    .createHmac("sha256", secret)
    .update(`${timestamp}\n${secret}`, "utf8")
    .digest("base64");
  const connector = webhook.includes("?") ? "&" : "?";
  return `${webhook}${connector}timestamp=${timestamp}&sign=${encodeURIComponent(signature)}`;
}

export async function sendDingTalkText(content: string): Promise<boolean> {
  const webhook = (process.env.DINGTALK_WEBHOOK ?? "").trim();
  const secret = (process.env.DINGTALK_SECRET ?? "").trim();
  if (!webhook) {
    return false;
  }

  try {
    const response = await fetch(buildSignedWebhook(webhook, secret), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ msgtype: "text", text: { content } }),
    });
    return response.ok;
  } catch {
    return false;
  }
}
