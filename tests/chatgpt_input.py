"""Run with: python -m pip install playwright && python tests/chatgpt_input.py.

Uses installed Chrome and local fixtures; does not send questions to ChatGPT.
"""
from pathlib import Path
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "content-scripts/chatgpt.js").read_text(encoding="utf-8")


def setup(page, html):
    page.goto("about:blank")
    page.set_content(html)
    page.evaluate("""() => {
      window.chrome = { runtime: {
        onMessage: { addListener(fn) { window.receiveMessage = fn; } },
        sendMessage: async () => ({})
      }};
      window.sent = false;
    }""")
    page.add_script_tag(content=SCRIPT)


with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    for editor in [
        '<div id="prompt-textarea" contenteditable="true"><p>Old draft</p></div>',
        '<textarea id="prompt-textarea">Old draft</textarea>',
        '<div contenteditable="true" data-composer-markdown role="textbox" class="ProseMirror"><p data-placeholder="Ask ChatGPT">Old draft</p></div>',
    ]:
        setup(page, f'<form>{editor}<button type="button" id="composer-submit-button" disabled>Send</button></form>')
        page.evaluate("""() => {
          const input = document.querySelector('[contenteditable], textarea');
          const button = document.querySelector('button');
          input.addEventListener('input', () => {
            window.editorState = input.value ?? input.innerText;
            setTimeout(() => { button.disabled = false; }, 700);
          });
          button.onclick = () => { window.sent = true; };
        }""")
        result = page.evaluate(r"""() => new Promise(resolve => receiveMessage({
          type: 'receiveQuestion', question: {
            type: 'multiple_choice', question: 'Is <b>x</b> < y & z?\nSecond line',
            options: ['Yes', 'No']
          }
        }, {}, resolve))""")
        assert result["received"], result
        assert page.evaluate("sent")
        state = page.evaluate("editorState")
        assert 'Is <b>x</b> < y & z?' in state and 'Second line' in state, state
        assert 'Old draft' not in state
        assert page.locator('[contenteditable] b, textarea b').count() == 0
        print('PASS: editor state, literal text, draft replacement, delayed send:', editor.split('>')[0])

    setup(page, '<form id="composer"></form>')
    page.evaluate("""() => setTimeout(() => {
      document.getElementById('composer').innerHTML =
        '<div id="prompt-textarea" contenteditable="true"></div>' +
        '<button type="button" data-testid="send-button">Send</button>';
      document.querySelector('button').onclick = () => { window.sent = true; };
    }, 500)""")
    page.evaluate("insertQuestion({type: 'fill_in_the_blank', question: 'Hello'})")
    assert page.evaluate("sent")
    print('PASS: delayed composer and original send selector')

    setup(page, '<button aria-label="Send" id="unrelated">Unrelated</button><div data-composer-body><div contenteditable="true" data-composer-markdown role="textbox" class="ProseMirror"><p data-placeholder="Ask ChatGPT"><br></p></div><button type="submit" aria-label="Send">Send</button></div>')
    page.evaluate("""() => {
      document.querySelector('[data-composer-body] button').onclick = () => { window.sent = true; };
      document.getElementById('unrelated').onclick = () => { throw new Error('Wrong send button'); };
    }""")
    page.evaluate("insertQuestion({type: 'fill_in_the_blank', question: 'Hello'})")
    assert page.evaluate('sent')
    assert page.evaluate("() => { let reply; receiveMessage({type:'ping'}, {}, r => reply=r); return reply.received; }")
    print('PASS: observed ChatGPT markup, scoped Send button, and listener ping')

    setup(page, '<div id="prompt-textarea" contenteditable="true"></div><button id="composer-submit-button" data-testid="stop-button" aria-label="Stop streaming">Stop</button>')
    page.evaluate("""() => {
      window.stopClicked = false;
      document.querySelector('button').onclick = () => { window.stopClicked = true; };
      window.realWait = waitForComposerElement;
      waitForComposerElement = (find, error) => realWait(find, error, 200);
    }""")
    result = page.evaluate("""() => new Promise(resolve => receiveMessage({
      type: 'receiveQuestion', question: {type: 'fill_in_the_blank', question: 'Hello'}
    }, {}, resolve))""")
    assert result['received'] is False
    assert 'send button' in result['error']
    assert not page.evaluate('stopClicked')
    print('PASS: stop control excluded and timeout reported')

    page.goto('about:blank')
    page.evaluate("""() => {
      const event = {addListener() {}};
      window.injections = [];
      window.chrome = {
        runtime: {onMessage: event},
        storage: {sync: {get: async () => ({aiModel:'chatgpt'})}},
        tabs: {onActivated:event, onRemoved:event, query:async()=>[],
          sendMessage:async()=>({received:true})},
        scripting: {executeScript:async config=>injections.push(config)}
      };
    }""")
    page.add_script_tag(content=(ROOT / 'background/background.js').read_text(encoding='utf-8'))
    page.evaluate('ensureChatGPTListener(123)')
    assert page.evaluate('injections.length') == 0
    page.evaluate("""() => {chrome.tabs.sendMessage = async () => {
      throw new Error('Could not establish connection. Receiving end does not exist.');
    }}""")
    page.evaluate('ensureChatGPTListener(123)')
    assert page.evaluate('injections') == [{'target': {'tabId': 123}, 'files': ['content-scripts/chatgpt.js']}]
    page.evaluate("""() => {chrome.tabs.sendMessage = async () => {throw new Error('Tab closed');}}""")
    assert page.evaluate("ensureChatGPTListener(123).catch(e=>e.message)") == 'Tab closed'
    assert page.evaluate('injections.length') == 1
    print('PASS: missing listener recovered; existing listener and other errors do not reinject')
    assert not errors, errors
    browser.close()
