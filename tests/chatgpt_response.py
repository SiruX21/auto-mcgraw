"""Run with python tests/chatgpt_response.py (requires playwright and Chrome)."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

SCRIPT = (Path(__file__).resolve().parents[1] / 'content-scripts/chatgpt.js').read_text(encoding='utf-8')

with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome', headless=True)
    page = browser.new_page()
    page.set_content('<main></main>')
    page.evaluate("""() => {
      window.deliveries = [];
      window.chrome = {runtime: {onMessage:{addListener(){}},
        sendMessage:async message=>{ deliveries.push(message); }}};
    }""")
    page.add_script_tag(content=SCRIPT)
    answer = {'answer': ['A -> braces {inside} and "quotes"', 'B -> line\nbreak'], 'explanation': 'Keep  two spaces and ​ text.'}
    source = json.dumps(answer)
    for markup in [
        '<div data-message-author-role="assistant"><pre><code class="language-json"></code></pre></div>',
        '<div data-chatgpt-selection-message-id="new"><div data-markdown-text-style="assistant-message"><pre><code></code></pre></div></div>',
        '<div data-message-author-role="assistant"><div data-chatgpt-selection-message-id="mixed"><div data-markdown-text-style="assistant-message"><pre><code></code></pre></div></div></div>',
    ]:
        page.evaluate('(html)=>document.querySelector("main").innerHTML=html', markup)
        page.evaluate("""source => {
          const code=document.querySelector('code');
          // Match the live viewer's syntax-highlight spans, without a language class.
          for(const part of [source.slice(0,20),source.slice(20)]) {
            const span=document.createElement('span');span.textContent=part;code.append(span);
          }
        }""", source)
        assert page.evaluate('getAssistantMessages().length') == 1
        assert page.evaluate('extractAnswer(getAssistantMessages()[0])') == answer
    print('PASS: legacy, current formatted JSON, nested markup counted once, exact string preservation')

    assert page.evaluate('text=>parseAnswerJSON(text)', 'Intro {not JSON} then ```json\n'+source+'\n``` trailing {noise}') == answer
    for invalid in ['{"answer":"unfinished', '{"answer":"raw\nnewline"}', '{"explanation":"no answer"}']:
        assert page.evaluate('text=>parseAnswerJSON(text)', invalid) is None
    for value in [False, 0, '']:
        assert page.evaluate('text=>parseAnswerJSON(text)', json.dumps({'answer': value})) == {'answer': value}
    print('PASS: surrounding prose, braces in strings, false/zero answers; malformed JSON rejected')

    page.evaluate('messagesAtQuestion=new Set(getAssistantMessages().map(getMessageIdentity)); startObserving()')
    page.wait_for_timeout(600)
    assert page.evaluate('deliveries.length') == 0
    page.evaluate("""() => {
      const stop=document.createElement('button');stop.setAttribute('aria-label','Stop');document.body.append(stop);
      document.querySelector('main').insertAdjacentHTML('beforeend',
        '<div data-chatgpt-selection-message-id="next"><div data-markdown-text-style="assistant-message"><pre><code id="next"></code></pre></div></div>');
      document.getElementById('next').textContent='{"answer":"partial';
    }""")
    page.wait_for_timeout(600)
    assert page.evaluate('deliveries.length') == 0
    page.evaluate('text=>document.getElementById("next").textContent=text', source)
    page.wait_for_timeout(600)
    assert page.evaluate('deliveries.length') == 0
    page.evaluate('document.querySelector("button").remove()')
    page.wait_for_function('deliveries.length===1')
    assert json.loads(page.evaluate('deliveries[0].response')) == answer
    page.evaluate('document.body.append(document.createElement("div"))')
    page.wait_for_timeout(600)
    assert page.evaluate('deliveries.length') == 1
    print('PASS: old answers ignored, partial/streaming responses withheld, completed answer delivered once')

    # Reproduce the live failure: ChatGPT keeps only a window of messages in
    # the DOM, so a new completed reply need not increase the visible count.
    for remaining in [5, 2]:
        page.evaluate("""() => {
          resetObservation(); deliveries.length=0;
          document.querySelector('main').innerHTML='';
          for(let i=0;i<5;i++) document.querySelector('main').insertAdjacentHTML('beforeend',
            `<div data-chatgpt-selection-message-id="old-${i}"><div data-markdown-text-style="assistant-message"><pre><code>{"answer":"old"}</code></pre></div></div>`);
          messagesAtQuestion=new Set(getAssistantMessages().map(getMessageIdentity));
          startObserving();
          // A rerender of the old latest message must not look like a new answer.
          const last=document.querySelector('main').lastElementChild;
          last.replaceWith(last.cloneNode(true));
        }""")
        page.wait_for_timeout(600)
        assert page.evaluate('deliveries.length') == 0
        page.evaluate("""remaining => {
          const main=document.querySelector('main');
          while(main.children.length>=remaining) main.firstElementChild.remove();
          main.insertAdjacentHTML('beforeend', '<div data-chatgpt-selection-message-id="fresh"><div data-markdown-text-style="assistant-message"><pre><code>{"answer":"new"}</code></pre></div></div>');
        }""", remaining)
        page.wait_for_function('deliveries.length===1')
        assert json.loads(page.evaluate('deliveries[0].response')) == {'answer': 'new'}
        assert page.evaluate('getAssistantMessages().length') == remaining
    print('PASS: new reply detected with unchanged/decreased visible count; old rerenders ignored')
    browser.close()
