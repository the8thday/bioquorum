/* Reopen a research project after an actual process kill. BQ_AWAY_SECONDS=3600 is the release run. */
const assert=require("node:assert/strict");
const fs=require("node:fs/promises");
const os=require("node:os");
const path=require("node:path");
const {spawn}=require("node:child_process");
const {once}=require("node:events");
const {setTimeout:delay}=require("node:timers/promises");
const {chromium}=require("playwright");
process.chdir(path.resolve(__dirname,"../.."));

async function main() {
  const root=await fs.mkdtemp(path.join(os.tmpdir(),"bq-background-"));
  const away=Number(process.env.BQ_AWAY_SECONDS || 0)*1000;
  let server,browser;
  async function start() {
    server=spawn(process.env.BQ_TEST_PYTHON || ".venv/bin/python",["tests/e2e/background_fixture.py"],{
      env:{...process.env,BQ_BACKGROUND_PROJECT:root},stdio:["ignore","pipe","inherit"]});
    let text="";
    for await (const chunk of server.stdout) {
      text+=chunk;
      const found=text.match(/WORKBENCH_URL=(http:\/\/127\.0\.0\.1:\d+\/)/);
      if (found) return found[1];
    }
    throw Error("Fixture exited before startup");
  }
  try {
    let url=await start();
    browser=await chromium.launch({channel:"chrome",headless:true});
    let page=await browser.newPage();
    await page.goto(url);
    await page.getByLabel("Research question").fill("Read the two independent batch records and calculate the combined sample count.");
    await page.getByRole("button",{name:"Run research",exact:true}).click();
    await page.locator("#composer .running-indicator").waitFor();
    await browser.close();
    browser=undefined;
    const closed=Date.now();
    for(let i=0; i<300; i++) {
      if(await fs.stat(path.join(root,"calculation-recorded")).catch(()=>null)) break;
      await delay(200);
    }
    await fs.stat(path.join(root,"calculation-recorded"));
    const stopped=once(server,"exit");
    server.kill("SIGKILL");await stopped;
    url=await start();
    console.log("Browser closed, research process killed after calculation and restarted.",new Date(closed).toISOString(),root);
    await delay(Math.max(0,closed+away-Date.now()));
    browser=await chromium.launch({channel:"chrome",headless:true});
    page=await browser.newPage();
    await page.goto(url);
    await page.getByRole("button",{name:"Resume research",exact:true}).click();
    await page.waitForFunction(()=>document.querySelector(".answer") || document.querySelector(".turn")?.textContent.includes("Failed"));

    const sid=new URL(page.url()).searchParams.get("session");
    const data=await (await page.request.get(url+"api/sessions/"+sid)).json();
    assert.equal(data.turns.length,1);
    assert.equal(data.turns[0].status,"completed",JSON.stringify(data.turns));
    assert.equal(data.activity.filter(e=>e.type==="subtask_finished" && e.outcome==="completed").length,2);
    assert.equal(data.activity.filter(e=>e.type==="run_finished").length,1);
    assert.match(await page.locator(".answer").innerText(),/\b8\b/);
    console.log("Restored two verified child reports and one real kernel run.",(Date.now()-closed)/1000,"seconds");
  } finally {
    if(browser) await browser.close();
    if(server && server.exitCode===null && server.signalCode===null) {const done=once(server,"exit");server.kill();await done;}
    await fs.rm(root,{recursive:true,force:true});
  }
}
main().catch(e=>{console.error(e);process.exitCode=1;});
