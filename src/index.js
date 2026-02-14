const crypto = require("crypto");
const express = require("express");
const { WebClient } = require("@slack/web-api");
require("dotenv").config();

const requiredEnvs = [
  "SLACK_BOT_TOKEN",
  "SLACK_SIGNING_SECRET",
  "SLACK_NOTIFICATION_CHANNEL_ID",
  "GITHUB_WEBHOOK_SECRET"
];

const missing = requiredEnvs.filter((key) => !process.env[key]);
if (missing.length > 0) {
  console.error(`Missing required env vars: ${missing.join(", ")}`);
  process.exit(1);
}

const app = express();
const port = Number(process.env.PORT || 3000);

const slack = new WebClient(process.env.SLACK_BOT_TOKEN);
const slackChannel = process.env.SLACK_NOTIFICATION_CHANNEL_ID;

const shouldMentionOnFailure = String(process.env.SLACK_MENTION_ON_FAILURE || "true") === "true";

function safeCompare(a, b) {
  const aBuf = Buffer.from(a || "");
  const bBuf = Buffer.from(b || "");
  if (aBuf.length !== bBuf.length) {
    return false;
  }
  return crypto.timingSafeEqual(aBuf, bBuf);
}

function verifyGitHubSignature(rawBody, signatureHeader) {
  const digest = `sha256=${crypto
    .createHmac("sha256", process.env.GITHUB_WEBHOOK_SECRET)
    .update(rawBody)
    .digest("hex")}`;
  return safeCompare(digest, signatureHeader);
}

function verifySlackSignature(rawBody, timestamp, signatureHeader) {
  if (!timestamp || !signatureHeader) return false;

  const nowSeconds = Math.floor(Date.now() / 1000);
  if (Math.abs(nowSeconds - Number(timestamp)) > 60 * 5) {
    return false;
  }

  const base = `v0:${timestamp}:${rawBody}`;
  const digest = `v0=${crypto
    .createHmac("sha256", process.env.SLACK_SIGNING_SECRET)
    .update(base)
    .digest("hex")}`;

  return safeCompare(digest, signatureHeader);
}

async function sendSlackMessage(text) {
  await slack.chat.postMessage({
    channel: slackChannel,
    text
  });
}

function toRepoLabel(repo) {
  if (!repo) return "unknown/repo";
  return repo.full_name || `${repo.owner?.login || "unknown"}/${repo.name || "repo"}`;
}

function buildGitHubMessage(event, payload) {
  const repo = toRepoLabel(payload.repository);
  const sender = payload.sender?.login || "unknown";

  if (event === "ping") {
    return `:satellite: GitHub webhook connected for *${repo}*`;
  }

  if (event === "push") {
    const branch = payload.ref?.replace("refs/heads/", "") || "unknown";
    const commits = (payload.commits || []).length;
    const compare = payload.compare || payload.repository?.html_url;
    const warning = shouldMentionOnFailure && /main|master/.test(branch) ? " <!channel>" : "";
    return `:git: *${repo}* push by *${sender}* to \`${branch}\` (${commits} commits) ${compare}${warning}`;
  }

  if (event === "pull_request") {
    const action = payload.action;
    const pr = payload.pull_request;
    if (!pr) return null;
    return `:pull_request: *${repo}* PR #${pr.number} *${action}* by *${sender}*\n<${pr.html_url}|${pr.title}>`;
  }

  if (event === "issues") {
    const issue = payload.issue;
    if (!issue) return null;
    return `:bookmark: *${repo}* issue #${issue.number} *${payload.action}* by *${sender}*\n<${issue.html_url}|${issue.title}>`;
  }

  if (event === "issue_comment") {
    const issue = payload.issue;
    const comment = payload.comment;
    if (!issue || !comment) return null;
    return `:speech_balloon: *${repo}* comment on #${issue.number} by *${sender}*\n<${comment.html_url}|Open comment>`;
  }

  return null;
}

app.get("/healthz", (_req, res) => {
  res.status(200).json({ ok: true });
});

app.post(
  "/github/webhook",
  express.raw({ type: "*/*" }),
  async (req, res) => {
    try {
      const raw = req.body.toString("utf8");
      const sig = req.header("x-hub-signature-256") || "";
      const event = req.header("x-github-event") || "unknown";

      if (!verifyGitHubSignature(raw, sig)) {
        return res.status(401).json({ ok: false, error: "invalid signature" });
      }

      const payload = JSON.parse(raw || "{}");
      const msg = buildGitHubMessage(event, payload);

      if (msg) {
        await sendSlackMessage(msg);
      }

      return res.status(200).json({ ok: true });
    } catch (error) {
      console.error("GitHub webhook error", error);
      return res.status(500).json({ ok: false, error: "internal error" });
    }
  }
);

app.post(
  "/slack/events",
  express.raw({ type: "application/json" }),
  async (req, res) => {
    try {
      const raw = req.body.toString("utf8");
      const timestamp = req.header("x-slack-request-timestamp") || "";
      const signature = req.header("x-slack-signature") || "";

      if (!verifySlackSignature(raw, timestamp, signature)) {
        return res.status(401).json({ ok: false, error: "invalid signature" });
      }

      const payload = JSON.parse(raw || "{}");

      if (payload.type === "url_verification") {
        return res.status(200).json({ challenge: payload.challenge });
      }

      if (payload.type === "event_callback" && payload.event?.type === "app_mention") {
        await sendSlackMessage(
          `:robot_face: Connected. This agent is active for GitHub notifications in this repo.`
        );
      }

      return res.status(200).json({ ok: true });
    } catch (error) {
      console.error("Slack event error", error);
      return res.status(500).json({ ok: false, error: "internal error" });
    }
  }
);

app.listen(port, () => {
  console.log(`Slack/GitHub agent listening on :${port}`);
});
