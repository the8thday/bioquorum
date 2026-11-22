/* Measure real browser open, scroll, and SSE reconnect on the fixed 10,000-event project. */
const {chromium}=require("playwright");
const {spawn}=require("node:child_process");
const {once}=require("node:events");
const {performance}=require("node:perf_hooks");
const assert=require("node:assert/strict");
async function main() {
  const server=spawn(".venv/bin/python",["evals/v12_long_session.py",process.argv[2],"--serve"],{stdio:["ignore","pipe","inherit"]});
  let browser;
  try {
    let url;
    for await (const chunk of server.stdout) {
      url=String(chunk).trim();
      if (url.startsWith("http://")) break;
    }
    assert.ok(url?.startsWith("http://"),"Fixture did not start");
    browser=await chromium.launch({channel:"chrome",headless:true});
    const page=await browser.newPage();
    const samples=[];
    for(let i=0;i<3;i++){
      const start=performance.now();
      await page.goto(url);
      await page.locator(".answer").last().waitFor();
      const open=performance.now()-start;
      const scroll=await page.evaluate(async()=>{
        const el=document.querySelector("#record").parentElement;
        el.scrollTop=el.scrollHeight;
        if (el.scrollTop<=0) throw Error("Research record did not overflow the viewport");
        await new Promise(r=>requestAnimationFrame(r));
        const start=performance.now();
        el.scrollTop=0;
        await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
        if(el.scrollTop!==0) throw Error("Research record did not reach the top");
        return performance.now()-start;
      });
      const reconnect=await page.evaluate(async()=>{
        const sid=new URL(location.href).searchParams.get("session");
        const start=performance.now();
        const s=new EventSource("/api/sessions/"+sid+"/events?after=9899");
        return await new Promise((resolve,reject)=>{
          const timer=setTimeout(()=>{s.close();reject(Error("Cursor did not advance"));},10000);
          s.addEventListener("change",event=>{
            if(event.lastEventId==="9999"){
              s.close();clearTimeout(timer);resolve(performance.now()-start);
            }
          });
        });
      });
      samples.push({open_ms:open,scroll_ms:scroll,sse_100_ms:reconnect});
    }
    if (process.env.BQ_TEST_PAGINATION) {
      assert.equal(await page.locator(".turn").count(),30);
      await page.getByRole("button",{name:"Show earlier research"}).click();
      await page.getByText("Inspect recorded batch 40",{exact:true}).waitFor();
      assert.equal(await page.locator(".turn").count(),60);
      await page.getByRole("button",{name:"Research tasks",exact:true}).click();
      await page.getByRole("button",{name:"Inspect recorded batch 0",exact:true}).click();
      const earliest=page.locator(".turn").filter({hasText:"Inspect recorded batch 0"});
      await earliest.waitFor();
      const index=await earliest.getAttribute("id");
      assert.equal(new URL(page.url()).hash,"#"+index);
      assert.ok(await earliest.evaluate(el=>{const r=el.getBoundingClientRect();return r.top<innerHeight && r.bottom>0;}));
    }
    assert.equal(samples.length,3);
    console.log(JSON.stringify(samples));
  } finally {
    if(browser)await browser.close();
    if(server.exitCode===null){const done=once(server,"exit");server.kill();await done;}
  }
}
main().catch(e=>{console.error(e);process.exitCode=1;});
