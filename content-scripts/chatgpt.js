let hasResponded = false;
let messagesAtQuestion = new Set();
let activeRequestId = null;
let observationStartTime = 0;
let observationTimeout = null;
let observationInterval = null;
let observer = null;

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "ping") {
    sendResponse({ received: true });
    return false;
  }
  if (message.type === "receiveQuestion") {
    resetObservation();
    activeRequestId = message.requestId || null;

    messagesAtQuestion = new Set(getAssistantMessages().map(getMessageIdentity));
    hasResponded = false;

    insertQuestion(message.question)
      .then(() => {
        sendResponse({ received: true, status: "processing" });
      })
      .catch((error) => {
        sendResponse({ received: false, error: error.message });
      });

    return true;
  }
});

function resetObservation() {
  hasResponded = false;
  if (observationInterval) {
    clearInterval(observationInterval);
    observationInterval = null;
  }
  if (observationTimeout) {
    clearTimeout(observationTimeout);
    observationTimeout = null;
  }
  if (observer) {
    observer.disconnect();
    observer = null;
  }
}

async function insertQuestion(questionData) {
  const { type, question, options, previousCorrection } = questionData;
  let text = `Type: ${type}\nQuestion: ${question}`;

  if (
    previousCorrection &&
    previousCorrection.question &&
    previousCorrection.correctAnswer
  ) {
    text =
      `CORRECTION FROM PREVIOUS ANSWER: For the question "${
        previousCorrection.question
      }", your answer was incorrect. The correct answer was: ${JSON.stringify(
        previousCorrection.correctAnswer
      )}\n\nNow answer this new question:\n\n` + text;
  }

  if (type === "matching") {
    text +=
      "\nPrompts:\n" +
      options.prompts.map((prompt, i) => `${i + 1}. ${prompt}`).join("\n");
    text +=
      "\nChoices:\n" +
      options.choices.map((choice, i) => `${i + 1}. ${choice}`).join("\n");
    text +=
      '\n\nPlease match each prompt with the correct choice. Set "answer" to an array of strings using the exact format \'Prompt -> Choice\'. Include one entry per prompt, use exact prompt and choice text, and use each choice at most once.';
  } else if (type === "fill_in_the_blank") {
    text +=
      "\n\nThis is a fill in the blank question. If there are multiple blanks, provide answers as an array in order of appearance. For a single blank, you can provide a string.";
  } else if (options && options.length > 0) {
    text +=
      "\nOptions:\n" + options.map((opt, i) => `${i + 1}. ${opt}`).join("\n");
    text +=
      "\n\nIMPORTANT: Your answer must EXACTLY match one of the above options. Do not include numbers in your answer. If there are periods, include them.";
  }

  text +=
    '\n\nIMPORTANT: Your answer should be in a JSON code block.' +
    '\n\nPlease provide your answer in JSON format with keys "answer" and "explanation". Explanations should be no more than one sentence. DO NOT acknowledge the correction in your response, only answer the new question.';

  const inputArea = await waitForComposerElement(
    () => Array.from(document.querySelectorAll(
      '#prompt-textarea, [contenteditable="true"][data-composer-markdown], [contenteditable="true"].ProseMirror[role="textbox"], [contenteditable="true"][data-placeholder], textarea[name="prompt-textarea"]'
    )).find((element) =>
      isComposerElementReady(element) &&
      (element.isContentEditable || element.tagName === "TEXTAREA") &&
      !element.readOnly
    ),
    "ChatGPT input is unavailable. Open a chat and wait for it to finish loading."
  );

  inputArea.focus();
  if (inputArea.tagName === "TEXTAREA") {
    const setter = Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype, "value"
    ).set;
    setter.call(inputArea, text);
    inputArea.dispatchEvent(new Event("input", { bubbles: true }));
  } else {
    // Use the browser's editing transaction so rich-text editors update their
    // internal state. Assigning innerHTML only changes their rendered DOM.
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(inputArea);
    selection.removeAllRanges();
    selection.addRange(range);
    if (!document.execCommand("insertText", false, text)) {
      throw new Error("ChatGPT rejected text entry. Refresh the ChatGPT tab and try again.");
    }
  }

  const sendButton = await waitForComposerElement(
    () => {
      if (!inputArea.isConnected) return null;
      const composer = inputArea.closest("form, [data-composer-body]") || document;
      return Array.from(composer.querySelectorAll(
        '[data-testid="send-button"], button#composer-submit-button, button[aria-label="Send prompt"], button[aria-label="Send message"], button[aria-label="Send"]'
      )).find((button) =>
        isComposerElementReady(button) &&
        button.getAttribute("data-testid") !== "stop-button" &&
        !/stop/i.test(button.getAttribute("aria-label") || "")
      );
    },
    "ChatGPT's send button did not become ready. Check the chat for a busy response or an error."
  );
  startObserving();
  sendButton.click();
}

function isComposerElementReady(element) {
  return element.getClientRects().length > 0 &&
    getComputedStyle(element).visibility !== "hidden" &&
    !element.disabled && element.getAttribute("aria-disabled") !== "true";
}

async function waitForComposerElement(findElement, errorMessage, timeout = 15000) {
  const deadline = Date.now() + timeout;
  do {
    const element = findElement();
    if (element) return element;
    await new Promise((resolve) => setTimeout(resolve, 100));
  } while (Date.now() < deadline);
  throw new Error(errorMessage);
}

// ChatGPT uses either the legacy author-role container or markdown roots
// inside a selection-message container. Count each message only once.
function getAssistantMessages() {
  const roots = document.querySelectorAll(
    '[data-message-author-role="assistant"], [data-markdown-text-style="assistant-message"]'
  );
  return [...new Set(Array.from(roots, (root) =>
    root.closest('[data-message-author-role="assistant"]') ||
    root.closest('[data-chatgpt-selection-message-id]') || root
  ))];
}

function getMessageIdentity(message) {
  // ChatGPT virtualizes long conversations: new replies can replace old DOM
  // entries without increasing the number of visible messages. IDs also
  // survive React replacing a message's DOM node.
  return message.getAttribute("data-message-id") ||
    message.getAttribute("data-chatgpt-selection-message-id") ||
    message.querySelector("[data-message-id]")?.getAttribute("data-message-id") ||
    message;
}

function parseAnswerJSON(text) {
  // Find complete JSON objects without confusing braces in quoted strings
  // or explanatory text around the object with the response itself.
  let start = -1;
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (start < 0) {
      if (char !== "{") continue;
      start = i;
      depth = 1;
      continue;
    }
    if (inString) {
      if (escaped) escaped = false;
      else if (char === "\\") escaped = true;
      else if (char === '\"') inString = false;
      continue;
    }
    if (char === '\"') inString = true;
    else if (char === "{") depth++;
    else if (char === "}" && --depth === 0) {
      try {
        const parsed = JSON.parse(text.slice(start, i + 1));
        if (Object.prototype.hasOwnProperty.call(parsed, "answer")) return parsed;
      } catch (error) {
        // Incomplete or invalid JSON must never be forwarded to McGraw Hill.
      }
      start = -1;
    }
  }
  return null;
}

function extractAnswer(message) {
  // Syntax highlighting splits text across spans and may omit language-json.
  // textContent reassembles the source without visual wrapping or UI labels.
  for (const code of message.querySelectorAll("pre code")) {
    const parsed = parseAnswerJSON(code.textContent);
    if (parsed) return parsed;
  }
  return parseAnswerJSON(message.textContent);
}

function startObserving() {
  observationStartTime = Date.now();
  console.info("[Auto-McGraw] Waiting for a new ChatGPT response", {
    previousMessages: messagesAtQuestion.size,
  });
  observationTimeout = setTimeout(() => {
    if (!hasResponded) {
      console.error("[Auto-McGraw] Timed out waiting for a valid ChatGPT JSON answer.", {
        visibleMessages: getAssistantMessages().length,
        previousMessages: messagesAtQuestion.size,
      });
      resetObservation();
    }
  }, 180000);

  const checkResponse = () => {
    if (hasResponded) return;
    const messages = getAssistantMessages();
    if (!messages.length) return;
    const latestMessage = messages[messages.length - 1];
    if (messagesAtQuestion.has(getMessageIdentity(latestMessage))) return;
    const generating = document.querySelector(
      '[data-testid="stop-button"], button[aria-label="Stop"], button[aria-label="Stop generating"], button[aria-label="Stop streaming"]'
    );
    if (generating || latestMessage.matches('[data-is-streaming="true"]') ||
        latestMessage.querySelector('.result-streaming, [data-is-streaming="true"]')) return;

    const parsed = extractAnswer(latestMessage);
    if (!parsed) return;
    hasResponded = true;
    console.info("[Auto-McGraw] Parsed new ChatGPT response; forwarding to McGraw Hill.");
    chrome.runtime.sendMessage({
      type: "chatGPTResponse",
      response: JSON.stringify(parsed),
      requestId: activeRequestId,
    }).then(() => {
      resetObservation();
    }).catch((error) => {
      console.error("[Auto-McGraw] Error sending response:", error);
      resetObservation();
    });
  };

  observer = new MutationObserver(checkResponse);
  observer.observe(document.body, {
    childList: true,
    subtree: true,
    characterData: true,
    attributes: true,
    attributeFilter: ["data-is-streaming", "aria-label", "data-testid"],
  });
  // Completion can change without any further response-text mutations.
  observationInterval = setInterval(checkResponse, 500);
}
