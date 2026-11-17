"use strict";

(() => {
  const screenshot = document.getElementById("product-screenshot");
  const caption = document.getElementById("screenshot-caption");
  const views = {
    reader: {
      source: "assets/screenshot-reader.svg",
      alt: "Monospace demo inbox showing folders, a message list, and a formatted email reader.",
      caption: "A real client. An illustrative demo mailbox."
    },
    compose: {
      source: "assets/screenshot-compose.svg",
      alt: "Monospace demo composer with a Markdown reply, recipients, and attachment controls.",
      caption: "Write in Markdown. Send plain text + HTML."
    }
  };
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => {
      const view = views[button.dataset.view];
      screenshot.src = view.source;
      screenshot.alt = view.alt;
      caption.textContent = view.caption;
      document.querySelectorAll("[data-view]").forEach((candidate) => {
        const active = candidate === button;
        candidate.classList.toggle("active", active);
        candidate.setAttribute("aria-pressed", String(active));
      });
    });
  });

  const policies = {
    draft: {
      title: "A draft. Ready when you are.",
      description: "Agent sends are saved to your Drafts folder. Nothing is sent until you take it from there.",
      icon: "↳"
    },
    confirm: {
      title: "Queued. Waiting for your say.",
      description: "Agent sends go to your outbox. Review the message and attachments, then approve it yourself in the terminal.",
      icon: "◇"
    },
    allow: {
      title: "Sent. Because you allowed it.",
      description: "Agent sends are delivered immediately. Enable this per account when you want your agent to send without a review step.",
      icon: "↗"
    }
  };
  document.querySelectorAll("[data-policy]").forEach((button) => {
    button.addEventListener("click", () => {
      const policy = policies[button.dataset.policy];
      document.getElementById("policy-title").textContent = policy.title;
      document.getElementById("policy-description").textContent = policy.description;
      document.getElementById("policy-icon").textContent = policy.icon;
      document.querySelectorAll("[data-policy]").forEach((candidate) => {
        const active = candidate === button;
        candidate.classList.toggle("active", active);
        candidate.setAttribute("aria-pressed", String(active));
      });
    });
  });

  document.querySelectorAll("[data-copy]").forEach((button) => {
    button.addEventListener("click", async () => {
      const source = document.getElementById(button.dataset.copy);
      const status = document.getElementById("copy-status");
      try {
        if (!navigator.clipboard || !window.isSecureContext) throw new Error("Clipboard unavailable");
        await navigator.clipboard.writeText(source.textContent.trim());
        button.textContent = "Copied ✓";
        status.textContent = "Installation command copied. Paste it into your terminal to get started.";
        window.setTimeout(() => { button.textContent = "Copy ⧉"; }, 2500);
      } catch {
        const range = document.createRange();
        range.selectNodeContents(source);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        status.textContent = "Command selected. Press Ctrl+C or ⌘C to copy, then paste it into your terminal.";
      }
    });
  });
})();
