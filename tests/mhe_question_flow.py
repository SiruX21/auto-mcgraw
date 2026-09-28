"""Regression tests for delayed McGraw Hill transitions and stale AI replies."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

SCRIPT = (Path(__file__).resolve().parents[1] / 'content-scripts/mheducation.js').read_text(encoding='utf-8')

with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome', headless=True)
    page = browser.new_page()
    page.on('pageerror', lambda e: print('PAGE ERROR:', e, flush=True))
    page.set_content('<awd-header><div class="header__navigation"></div></awd-header><main></main>')
    page.evaluate("""() => {
      window.sent=[];window.submits=0;window.nextClicks=0;
      window.chrome={storage:{sync:{get:(keys,cb)=>cb({})},onChanged:{addListener(){}}},
        runtime:{onMessage:{addListener(){},removeListener(){}},sendMessage:async m=>{sent.push(m);return {received:true};}}};
      if(!crypto.randomUUID) crypto.randomUUID=()=>Math.random().toString(36);
      window.showQuestion=(id,answer)=>{
        document.querySelector('main').innerHTML=`<div class="probe-container"><div data-probe-id="${id}"><div class="awd-probe-type-multiple_choice"><div class="prompt">Question ${id}</div><label><input type="radio"><span class="choiceText">${answer}</span></label></div></div></div><button disabled data-automation-id="confidence-buttons--high_confidence">High</button>`;
        document.querySelector('input').onclick=()=>{document.querySelector('[data-automation-id]').disabled=false;};
        document.querySelector('[data-automation-id]').onclick=()=>{
          submits++;
          document.querySelector('[data-automation-id]').disabled=true;
          document.querySelector('.probe-container').insertAdjacentHTML('beforeend','<div class="answer-container"><span class="choiceText">A</span></div>');
          const next=document.createElement('button');next.className='next-button';next.textContent='Next';
          next.onclick=()=>{nextClicks++;setTimeout(()=>showQuestion('second','B'),1600);};
          document.querySelector('main').append(next);
        };
      };
      showQuestion('first','A');
    }""")
    page.add_script_tag(content=SCRIPT)
    page.evaluate('isAutomating=true; checkForNextStep(); checkForNextStep()')
    assert page.evaluate('sent.filter(m=>m.type==="sendQuestionToChatGPT").length') == 1
    first = page.evaluate('pendingQuestion.requestId')
    page.evaluate("""id => {window.responseDone=processChatGPTResponse(JSON.stringify({answer:'A'}),id);}""", first)
    page.wait_for_function('nextClicks===1')
    # The old feedback remains for longer than the original fixed one-second delay.
    page.wait_for_timeout(1100)
    page.evaluate('checkForNextStep()')
    assert page.evaluate('sent.filter(m=>m.type==="sendQuestionToChatGPT").length') == 1
    assert page.evaluate('parseQuestion().options') == ['A']
    page.evaluate('responseDone')
    assert page.evaluate('sent.filter(m=>m.type==="sendQuestionToChatGPT").length') == 2
    assert page.evaluate('sent.at(-1).question.question') == 'Question second'
    assert page.evaluate('submits') == 1
    print('PASS: one request per question; delayed transition waits; feedback is excluded')

    second = page.evaluate('pendingQuestion.requestId')
    page.evaluate('id=>processChatGPTResponse(JSON.stringify({answer:"A"}),id)', first)
    assert not page.locator('input').is_checked()
    assert page.evaluate('pendingQuestion.requestId') == second
    page.evaluate('showQuestion("third","C")')
    page.evaluate('id=>processChatGPTResponse(JSON.stringify({answer:"B"}),id)', second)
    assert not page.locator('input').is_checked()
    assert page.evaluate('sent.at(-1).question.question') == 'Question third'
    print('PASS: late replies cannot fill a newer question or consume its request')

    third = page.evaluate('pendingQuestion.requestId')
    failure = page.evaluate('id=>processChatGPTResponse(JSON.stringify({answer:"Does not match"}),id).catch(e=>e.message)', third)
    assert 'did not match any input' in failure
    assert page.evaluate('submits') == 1
    print('PASS: unmatched answer reported immediately without clicking confidence')

    page.evaluate('checkForNextStep();pauseBeforeSubmit=true')
    current = page.evaluate('pendingQuestion.requestId')
    page.evaluate('id=>{window.cancelDone=processChatGPTResponse(JSON.stringify({answer:"C"}),id)}', current)
    page.wait_for_function('document.querySelector("input").checked')
    page.evaluate('isAutomating=false;pendingQuestion=null')
    page.evaluate('cancelDone')
    assert page.evaluate('submits') == 1
    assert page.evaluate('nextClicks') == 1
    print('PASS: stopping cancels pending advancement; manual-submit mode never auto-submits')

    page.evaluate("""() => {
      pauseBeforeSubmit=false;showQuestion('feedback','A');
      document.querySelector('input').click();
      document.querySelector('[data-automation-id]').click();
      sent.length=0;isAutomating=true;checkForNextStep();
    }""")
    page.wait_for_function('sent.some(m=>m.type==="sendQuestionToChatGPT")')
    assert page.evaluate('sent.filter(m=>m.type==="sendQuestionToChatGPT").length') == 1
    assert page.evaluate('sent.at(-1).question.question') == 'Question second'
    print('PASS: resuming on feedback advances without resending the graded question')
    browser.close()
