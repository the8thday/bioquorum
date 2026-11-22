/* Offline browser contract for the packaged workbench. Run with node. */
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const { once } = require("node:events");
const { chromium } = require("playwright");
process.chdir(require("node:path").resolve(__dirname,"../.."));

async function serverURL(process) {
  let buffer = "";
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(Error("Workbench did not start")), 15000);
    process.stdout.on("data", (chunk) => {
      buffer += chunk;
      const found = buffer.match(/WORKBENCH_URL=(http:\/\/127\.0\.0\.1:\d+\/)/);
      if (found) {
        clearTimeout(timer);
        resolve(found[1]);
      }
    });
    process.once("exit", (code) => {
      clearTimeout(timer);
      reject(Error("Workbench exited with status " + code));
    });
  });
}

async function sessionAction(page, name) {
  await page.getByRole("button", {name: "Session actions", exact: true}).click();
  await page.getByRole("button", {name, exact: true}).click();
}

async function verifyProjectNote(page) {
  const first = await page.context().newPage();
  await first.goto(page.url());
  const editors = [page, first];
  for (const tab of editors) {
    await sessionAction(tab, "Project note");
    await tab.getByRole("dialog", {name: "Project note"}).waitFor();
  }
  const accepted = "Separate biological and technical replicates.";
  await page.getByRole("textbox", {name: "Note", exact: true}).fill(accepted);
  await page.getByRole("button", {name: "Save project note"}).click();
  await page.getByText("Project note saved.").waitFor();
  await first.getByRole("textbox", {name: "Note", exact: true}).fill("Stale edit");
  await first.getByRole("button", {name: "Save project note"}).click();
  await first.getByRole("dialog", {name: "Project note"}).getByText(/The project note changed/).waitFor();
  await first.getByRole("button", {name: "Reload saved note"}).click();
  await first.waitForFunction((text) => document.getElementById("note-body").value === text, accepted);
  await first.close();
  await page.getByRole("button", {name: "Newer research", exact: true}).click();
  await page.getByRole("heading", {name: "Newer research", exact: true}).waitFor();
  await sessionAction(page, "Project note");
  assert.equal(await page.getByRole("textbox", {name: "Note", exact: true}).inputValue(), accepted);
  console.log("Project note shared across sessions; stale edit rejected");
}

async function verifyRealProject(page) {
  await page.getByRole("heading", {name: "Verify a cell-count calculation"}).waitFor();
  await page.getByRole("button", {name: "Rerun calculation"}).click();
  await page.getByText("Verification complete. Open the comparison under the claim.").waitFor();
  const last = page.getByText("Rerun · matched", {exact: true}).last();
  await last.click();
  const comparison = page.getByRole("table").last();
  assert.match(await comparison.innerText(), /Recorded.*New.*Comparison/s);
  assert.match(await comparison.innerText(), /68 cells.*68 cells.*Match/s);
  if (process.env.BQ_TEST_SCREENSHOT) await page.screenshot({path: process.env.BQ_TEST_SCREENSHOT});
  await sessionAction(page, "Export research bundle");
  const received = page.waitForEvent("download");
  await page.getByRole("link", {name: "Download research bundle"}).click();
  assert.match((await received).suggestedFilename(), /^bioquorum-.*\.zip$/);
  console.log("Workbench verification and bundle download passed");
}

async function verifyConversation(page) {
  const box = page.getByLabel("Research question");
  await page.getByRole("heading", {name: "Conversation fixture"}).waitFor();
  const sid = new URL(page.url()).searchParams.get("session");
  const button = name => page.getByRole("button", {name, exact: true});
  const model = page.getByRole("combobox", {name: "Worker model"});
  const restricted = model.locator('option[value="other:restricted"]');
  assert.equal(await restricted.evaluate(option => option.disabled), true);
  assert.match(await restricted.textContent(), /unavailable for public data/);
  for (const [q, spec] of [["First **question** for reference", "openai:fixture"], ["Second question", "deepseek:fixture"], ["Third question", "openai:fixture"]]) {
    if (q === "Second question") {
      await box.fill("Keep my existing draft");
      const paragraph = page.locator(".claim p").filter({has: page.locator("strong")}).first();
      const anchor = await paragraph.evaluate(el => {
        const bold = el.querySelector("strong").firstChild;
        const last = el.lastElementChild.lastChild;
        const range = document.createRange();range.setStart(bold,0);range.setEnd(last,last.length);
        const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
        return range.getBoundingClientRect().top;
      });
      const quote = button("Quote selection in question");
      await quote.waitFor();
      const pos = await quote.boundingBox();
      assert.ok(Math.abs(pos.y-anchor)<55, "quote action stays near selected line");
      await quote.click();
      const mini = page.getByRole("dialog", {name:"Reply to selection"});
      await mini.getByLabel("Follow-up question").fill(q);
      await mini.getByRole("button", {name:"Add to message"}).click();
      await page.getByRole("list",{name:"References"}).getByText(q,{exact:true}).waitFor();
      assert.equal(await box.inputValue(),"Keep my existing draft");
      await page.waitForFunction(() => document.activeElement === document.getElementById("question"));
    }
    await model.selectOption(spec);
    await box.fill(q === "Second question" ? "" : q);
    await button("Run research").click();
    if (q.startsWith("First ")) {
      await page.locator("#composer .running-indicator").waitFor();
      assert.notEqual(await page.locator("#composer .running-indicator").evaluate(el=>getComputedStyle(el,"::before").animationName),"none");
    }
    const answer = page.locator(".answer").filter({hasText: "Recorded answer to " + q});
    await answer.getByText(spec, {exact: true}).waitFor();
    if (q === "Third question") {
      const link = answer.getByRole("link", {name: "Trial registry"});
      assert.equal(await link.getAttribute("href"), "https://example.org/?query=17%20weeks");
      await answer.getByRole("link", {name: "DOI 10.1000/example"}).waitFor();
      assert.ok(!(await answer.innerText()).includes("https://"));
      const notes = answer.locator("details").filter({has: page.locator("summary", {hasText: "Unbound values or citations"})});
      assert.equal(await notes.count(), 1);
      assert.equal(await notes.evaluate(el => el.open), false);
      const reviewUrl = "**/api/sessions/*/answers/*/review";
      await page.route(reviewUrl, route=>route.fulfill({status:204}));
      await answer.getByLabel("Check recorded PMIDs and DOIs online").check();
      const review = page.waitForRequest(request=>request.url().endsWith("/review"));
      await answer.getByRole("button",{name:"Run review"}).click();
      assert.deepEqual((await review).postDataJSON(),{online_citations:true});
      await page.unroute(reviewUrl);
      await page.getByRole("button",{name:"Research tools",exact:true}).click();
      await page.getByRole("dialog",{name:"Research tools"}).getByRole("button",{name:"View artifacts"}).click();
      await page.getByRole("link",{name:"Download kernel notebook"}).waitFor();
    }
  }
  await model.selectOption("deepseek:fixture");
  await sessionAction(page, "Model context");
  const panel = page.getByRole("dialog", {name: "Model context"});
  await panel.getByText(/32,000 token window/).waitFor();
  await panel.getByRole("button", {name: "Compact now"}).click();
  await panel.getByText(/Events .*openai:fixture/).click();
  await panel.getByText("Earlier questions recorded.").waitFor();
  await panel.getByRole("button", {name: "Close context"}).click();
  await box.fill("Wait for permission");
  await button("Run research").click();
  await page.getByRole("heading", {name: "Permission requested"}).waitFor();
  assert.equal(await page.locator("#composer .running-indicator").count(),0);
  await button("Stop research").click();
  await page.getByText("Interrupted", {exact: true}).waitFor();
  await box.fill("Changed direction");
  await button("Run research").click();
  await page.locator(".answer").getByText("Recorded answer to Changed direction", {exact: true}).waitFor();
  await page.waitForFunction(() => !document.getElementById("question").disabled);
  const record = await page.locator("#record").innerText();
  await page.reload();
  await page.locator(".answer").last().waitFor();
  assert.equal(await page.locator("#record").innerText(), record);
  await sessionAction(page, "Rename session");
  await page.getByLabel("Session title").fill("Research log");
  await button("Save name").click();
  await page.getByRole("heading", {name: "Research log"}).waitFor();
  const search = page.getByRole("searchbox", {name: "Search sessions"});
  await search.fill("THIRD QUESTION");
  await button("Research log").waitFor();
  await page.getByText("1 matching session", {exact: true}).waitFor();
  await search.fill("Unrelated query");
  await page.getByText("No matching sessions.").waitFor();
  await search.fill("");
  await sessionAction(page, "Archive session");
  await page.getByRole("dialog").getByRole("button", {name: "Archive session"}).click();
  await page.getByRole("heading", {name: "Start a research session"}).waitFor();
  await page.reload();
  await page.getByText("No sessions in this project yet.").waitFor();
  const stored = await (await page.request.get(new URL("/api/sessions/" + sid, page.url()).href)).json();
  assert.equal(stored.turns.length, 5);
  assert.equal(stored.turns[3].status, "interrupted");
  console.log("Conversation, interruption, resume, refresh, search and archive passed");
}

(async () => {
  const server = spawn(process.env.BQ_TEST_PYTHON || ".venv/bin/python",
    ["tests/e2e/serve_workbench.py"], {stdio: ["ignore", "pipe", "inherit"]});
  let browser;
  try {
    const url = await serverURL(server);
    browser = await chromium.launch({
      args: (process.env.BQ_TEST_VIEWERS || process.env.BQ_TEST_HER2) ? ["--use-gl=angle","--use-angle=swiftshader","--enable-unsafe-swiftshader"] : [],
      channel: process.env.BQ_CHROME_CHANNEL || "chrome", headless: true,
      executablePath: process.env.BQ_CHROME_PATH,
    });
    const context = await browser.newContext({viewport: {width: 1440, height: 900}});
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    for (let attempt = 0; attempt < 20; attempt++) {
      try {
        await page.goto(url);
        break;
      } catch (error) {
        if (attempt === 19) throw error;
        await new Promise((resolve) => setTimeout(resolve, 50));
      }
    }
    if (process.env.BQ_TEST_TASKS) {
      const question = "First **question** in the background";
      await page.getByLabel("Research question").fill(question);
      await page.getByRole("button", {name:"Run research", exact:true}).click();
      await page.locator("#composer .running-indicator").waitFor();
      const location=page.url();
      await page.close();
      await context.addInitScript(() => {
        window.notifications = [];
        window.Notification = class {
          static permission = "default";
          static async requestPermission() { return this.permission = "granted"; }
          constructor(title, options) { this.title = title; this.options = options; window.notifications.push(this); }
          close() {}
        };
      });
      const returned=await context.newPage();
      await returned.goto(location);
      await returned.getByRole("button",{name:"Research tasks",exact:true}).click();
      const board=returned.getByRole("dialog",{name:"Research tasks"});
      await board.getByText("Conversation fixture · completed").waitFor();
      await board.getByRole("button",{name:question,exact:true}).click();
      await returned.locator(".answer").first().waitFor();
      await returned.getByRole("button",{name:"Workbench settings"}).click();
      const notifications=returned.getByLabel("Notify me of new inbox items while the workbench is open");
      assert.equal(await notifications.isChecked(),false);
      await notifications.check();
      const initialInbox=returned.waitForResponse(response=>new URL(response.url()).pathname==="/api/inbox");
      await returned.getByRole("button",{name:"Save preferences"}).click();
      await initialInbox;
      await returned.getByLabel("Research question").fill("Wait for permission");
      await returned.getByRole("button",{name:"Run research",exact:true}).click();
      await returned.getByRole("heading",{name:"Permission requested"}).waitFor();
      await returned.waitForFunction(()=>window.notifications.length===1,{},{timeout:25000});
      const notice=await returned.evaluate(()=>({title:window.notifications[0].title, options:window.notifications[0].options}));
      assert.deepEqual(notice,{title:"BioQuorum has new inbox items",options:{tag:"bioquorum-inbox"}});
      await returned.evaluate(()=>window.notifications[0].onclick());
      const inbox=returned.getByRole("dialog",{name:"Inbox"});
      await inbox.getByRole("button",{name:"download_file",exact:true}).click();
      await returned.getByRole("button",{name:"Deny",exact:true}).click();
      await returned.getByText("Permission decision recorded.",{exact:true}).first().waitFor();
      await returned.getByRole("button",{name:"Inbox",exact:true}).click();
      await inbox.getByText("No pending items.").waitFor();
      await inbox.getByRole("button",{name:"Close list"}).click();
      await returned.getByLabel("Research question").fill("Inspect the archive");
      await returned.getByLabel("Review a plan first").check();
      await returned.getByRole("button",{name:"Draft steps with planner"}).click();
      await returned.waitForFunction(()=>document.querySelector("#steps")?.value==="Read local material");
      await returned.reload();
      await returned.getByRole("button",{name:"Submit plan"}).waitFor();
      assert.equal(await returned.locator("#steps").inputValue(),"Read local material");
      await returned.getByRole("button",{name:"Submit plan"}).click();
      await returned.getByRole("button",{name:"Inbox",exact:true}).click();
      await inbox.getByText("Conversation fixture · Plan approval").waitFor();
      await inbox.getByRole("button",{name:"Inspect the archive"}).click();
      await returned.getByRole("button",{name:"Decline plan"}).click();
      await returned.getByText("Declined by researcher").waitFor();
      console.log("Reopened a completed background task, resolved permission through the inbox, and restored a planner draft.");
      return;
    }
    if (process.env.BQ_TEST_HER2) {
      await require("./her2.cjs")(page);
      assert.deepEqual(errors, []);
      return;
    }
    if (process.env.BQ_TEST_SCENARIO_D) {
      try { await require("./scenario_d.cjs")(page); }
      catch (error) { console.error(await page.locator("body").innerText()); throw error; }
      assert.deepEqual(errors, []);
      return;
    }
    if (process.env.BQ_TEST_VIEWERS) {
      await require("./viewers.cjs")(page);
      assert.deepEqual(errors,[]);
      return;
    }
    if (process.env.BQ_TEST_BILINGUAL) {
      await page.getByRole("button", {name:"7",exact:true}).click();
      const evidence = page.getByRole("dialog", {name:"total",exact:true});
      await evidence.getByRole("heading", {name:"R code"}).waitFor();
      assert.match(await evidence.innerText(), /bq_value\('total', 7L\)/);
      await evidence.getByRole("button", {name:"Close evidence"}).click();
      await page.getByRole("button", {name:"Project settings",exact:true}).click();
      const settings = page.getByRole("dialog",{name:"Project settings"});
      await settings.getByText("Runtime and project",{exact:true}).click();
      await settings.getByText("R packages (1)",{exact:true}).click();
      await settings.getByText("DESeq2 1.0.0",{exact:true}).waitFor();
      const sid = new URL(page.url()).searchParams.get("session");
      const response = await page.request.get(new URL("/api/sessions/"+sid+"/notebook",page.url()).href);
      assert.match(response.headers()["content-disposition"], /research-notebooks.zip/);
      const archive = await response.body();
      for (const name of ["research-python.ipynb","research-r.ipynb"]) assert.ok(archive.includes(Buffer.from(name)));
      assert.deepEqual(errors, []);
      console.log("R evidence, project package inventory and mixed notebook download passed");
      return;
    }
    if (process.env.BQ_TEST_MATERIALS) {
      try { await require("./materials.cjs")(page); }
      catch (error) { console.error(await page.locator("body").innerText()); throw error; }
      assert.deepEqual(errors, []);
      return;
    }
    if (process.env.BQ_TEST_COVERAGE) {
      await page.getByRole("heading", {name:"Conversation fixture"}).waitFor();
      const question = page.getByLabel("Research question");
      await question.fill("Assess this evidence");
      await page.getByRole("button",{name:"Run research",exact:true}).click();
      await page.getByText("Evidence coverage · closed").click();
      await page.getByText("Registry outcomes were not searched.").waitFor();
      await page.getByRole("button",{name:"Inbox",exact:true}).click();
      await page.getByRole("dialog",{name:"Inbox"}).getByRole("button",{name:"Which outcomes matter?"}).click();
      assert.match(new URL(page.url()).hash,/^#record-/);
      await page.getByRole("button",{name:"Discuss next step"}).click();
      await page.getByRole("list",{name:"References"}).getByText("Search the registry.").waitFor();
      await question.fill("Follow that step");
      await page.getByRole("button",{name:"Run research",exact:true}).click();
      await page.getByText("Follow-up reference received.",{exact:true}).waitFor();
      await page.reload();
      await page.getByText("Follow-up reference received.",{exact:true}).waitFor();
      console.log("Coverage ledger, first-pass discussion and recorded follow-up verified");
      return;
    }
    if (process.env.BQ_TEST_EXTENSIONS) {
      const button = (parent,name) => parent.getByRole("button",{name,exact:true});
      const dialog = page.getByRole("dialog",{name:"Workbench settings"});
      await button(page,"Home").click();
      await page.getByRole("heading",{name:"Start a research session"}).waitFor();
      await page.getByLabel("Research question").fill("Research from home");
      await button(page,"Run research").click();
      await page.locator(".answer").getByText("Recorded answer to Research from home",{exact:true}).waitFor();
      await button(page,"Skills").click();
      await dialog.getByRole("button",{name:"Enable skill-creator",exact:true}).waitFor();
      await button(dialog,"Install skill").click();
      await dialog.getByLabel("Skill file").setInputFiles({name:"SKILL.md",mimeType:"text/markdown",
        buffer:Buffer.from("---\nname: review-table\ndescription: Check a table header\n---\nInspect the column names.")});
      await button(dialog,"Install and enable").click();
      await dialog.getByText("review-table",{exact:true}).waitFor();
      await button(dialog,"Disable").click();
      await button(dialog,"Enable").click();
      await dialog.getByText("review-table enabled.",{exact:true}).waitFor();
      await button(dialog,"Close workbench settings").click();
      await page.getByLabel("Research question").fill("Use the installed table skill");
      await button(page,"Run research").click();
      await page.locator(".answer").getByText("The installed skill says to inspect column names.",{exact:true}).waitFor();
      await button(page,"MCP connectors").click();
      await button(dialog,"Add connector").click();
      await dialog.getByLabel("Connector name").fill("fixture");
      await dialog.getByLabel("Server URL").fill("https://example.org/mcp");
      await button(dialog,"Save connector").click();
      await dialog.waitFor({state:"hidden"});
      await button(page,"MCP connectors").click();
      await dialog.getByText("fixture",{exact:true}).waitFor();
      await button(dialog,"Disable").click();
      await button(dialog,"Close workbench settings").click();
      await page.reload();
      await button(page,"MCP connectors").click();
      await button(dialog,"Enable").waitFor();
      for (const width of [1024,390,320]) {
        await page.setViewportSize({width,height:844});
        assert.ok(await dialog.evaluate(el=>el.scrollWidth<=el.clientWidth),"settings fit at "+width);
      }
      await button(dialog,"Remove").click();
      await button(dialog,"Confirm removal").click();
      await dialog.getByText("No MCP connectors in this project.").waitFor();
      await button(dialog,"Skills").click();
      await button(dialog,"Remove").click();
      await button(dialog,"Confirm removal").click();
      await dialog.getByText("No skills enabled or installed for this project.").waitFor();
      await dialog.getByRole("button",{name:"Enable skill-creator",exact:true}).click();
      await dialog.getByText("skill-creator enabled.",{exact:true}).waitFor();
      assert.deepEqual(errors,[]);
      console.log("Home research, skill install and management, connector registration and persistence verified");return;
    }
    if (process.env.BQ_TEST_PROJECTS) {
      const prefs = page.getByRole("button",{name:"Workbench settings"});
      await prefs.click();
      await page.getByLabel("Conversation text size").selectOption("large");
      await page.getByLabel("Inspector when opening a session").selectOption("Usage");
      await page.getByRole("button",{name:"Save preferences"}).click();
      await page.getByRole("button", {name:"New project"}).click();
      await page.getByLabel("Project name").fill("Research library");
      await page.getByLabel("Project instructions").fill("Prefer donor-level evidence.");
      await page.getByRole("button", {name:"Create project"}).click();
      await page.waitForURL(/\/projects\/[A-Z0-9]+\//);
      const path = new URL(page.url()).pathname;
      await page.getByText("OPENAI_API_KEY · Detected by workbench").waitFor();
      const workbenchTools = page.getByRole("button",{name:"Research tools",exact:true});
      await workbenchTools.click();
      const guide = page.getByRole("dialog",{name:"Research tools"});
      await guide.getByText("Blocked for internal data").waitFor();
      assert.ok(await guide.getByRole("button",{name:"Inspect evidence"}).isDisabled());
      await guide.getByRole("button",{name:"Inspect runtime"}).click();
      await page.getByText(/Kernel isolation:/).waitFor();
      await page.keyboard.press("Escape");
      let directoryAvailable = false;
      await page.route("**/api/settings/openai-models/openai", route=>route.fulfill(directoryAvailable ?
        {json:["ft:research", "gpt-test-from-account"]} : {status:400,json:{detail:"Catalog temporarily unavailable"}}));
      await page.getByRole("button",{name:"Choose model"}).click();
      await page.getByText("Catalog temporarily unavailable").waitFor();
      directoryAvailable = true;
      await page.getByRole("button",{name:"Refresh models"}).click();
      await page.getByRole("combobox",{name:"Account models",exact:true}).selectOption("gpt-test-from-account");
      await page.getByRole("button",{name:"Save model",exact:true}).click();
      await page.locator("#settings-dialog").waitFor({state:"hidden"});
      await page.keyboard.press("Escape");
      await page.getByRole("region",{name:"Model setup"}).getByText("openai:gpt-test-from-account").waitFor();
      await page.getByRole("button", {name:"New session", exact:true}).click();
      await page.getByLabel("Session title").fill("Local session");
      await page.getByRole("button", {name:"Continue"}).click();
      await page.getByRole("heading", {name:"Local session"}).waitFor();
      await page.getByLabel("Project", {exact:true}).selectOption("/");
      await page.waitForURL(url);
      await page.getByLabel("Project", {exact:true}).selectOption(path);
      await page.getByRole("heading", {name:"Local session"}).waitFor();
      assert.equal(await page.locator(".shell").getAttribute("data-reading-size"),"large");
      await page.getByRole("region",{name:"Model usage"}).waitFor();
      const currentURL = page.url();
      const other = await page.request.post(new URL(path+"api/sessions",currentURL).href, {data:{title:"Other session"},headers:{Origin:new URL(currentURL).origin}});
      assert.ok(other.ok());
      await page.reload();
      await page.getByRole("button",{name:"Rename Other session",exact:true}).click();
      await page.getByLabel("Session title").fill("Renamed from rail");
      await page.getByRole("button",{name:"Save name",exact:true}).click();
      await page.getByRole("button",{name:"Rename Renamed from rail",exact:true}).waitFor();
      assert.equal(page.url(),currentURL);
      await page.getByRole("heading",{name:"Local session"}).waitFor();
      const projectSettings = page.getByRole("button", {name:"Project settings", exact:true});
      const profile = page.getByLabel("Research profile");
      const binding = page.getByLabel("Evidence binding", {exact:true});
      for (const [oldName, newName] of [["recommended", "minimal"], ["minimal", "recommended"]]) {
        await projectSettings.click();
        assert.equal(await profile.inputValue(), oldName);
        assert.equal(await binding.isChecked(), oldName === "recommended");
        await profile.selectOption(newName);
        await page.getByRole("button", {name:"Save settings"}).click();
        await page.locator("#settings-dialog").waitFor({state:"hidden"});
        await page.reload();
      }
      await page.getByRole("button",{name:"Models & accounts"}).click();
      await page.getByRole("heading",{name:"Add a model"}).waitFor();
      await page.getByRole("combobox",{name:"Connection",exact:true}).selectOption("chatgpt");
      await page.getByRole("button",{name:"Continue with ChatGPT"}).waitFor();
      await page.getByRole("combobox",{name:"Connection",exact:true}).selectOption("deepseek");
      await page.getByLabel("Model ID",{exact:true}).fill("research-model");
      await page.getByRole("button",{name:"Save model", exact:true}).click();
      await page.locator("#settings-dialog").waitFor({state:"hidden"});
      await page.getByRole("button",{name:"Models & accounts"}).click();
      await page.getByText("Current research model · deepseek:research-model").waitFor();
      await page.getByText("Project models and fallbacks",{exact:true}).click();
      for (const role of ["Research model", "reviewer", "planner", "summarizer"]) {
        await page.getByRole("combobox",{name:role,exact:true}).waitFor();
      }
      await page.keyboard.press("Escape");
      const chatSettings = page.getByRole("button",{name:"Chat settings"});
      await page.getByLabel("Research question").fill("Draft survives settings");
      await chatSettings.click();
      const quick = page.getByRole("dialog",{name:"Chat settings"});
      await quick.getByLabel("Follow-up references").check();
      await quick.getByRole("button",{name:"Save settings"}).click();
      await quick.waitFor({state:"hidden"});
      await page.keyboard.press("Escape");
      assert.equal(await page.getByLabel("Research question").inputValue(),"Draft survives settings");
      await page.getByRole("button",{name:"Project settings",exact:true}).click();
      assert.ok(await page.getByLabel("Follow-up references").isChecked());
      assert.equal(await profile.inputValue(), "custom");
      assert.equal(await page.getByLabel("Project instructions").inputValue(), "Prefer donor-level evidence.");
      await page.getByLabel("Project name").fill("Renamed project");
      await page.getByLabel("Project instructions").fill("Summarize by donor.");
      await page.getByRole("button",{name:"Save settings"}).click();
      await page.locator("#settings-dialog").waitFor({state:"hidden"});
      await projectSettings.click();
      await page.getByRole("button",{name:"Delete this project…"}).click();
      await page.getByRole("dialog",{name:"Delete project?"}).getByText("Renamed project").waitFor();
      await page.getByRole("button",{name:"Cancel",exact:true}).click();
      await page.keyboard.press("Escape");
      assert.equal(await page.getByRole("button",{name:"Delete project…",exact:true}).count(),0);
      await page.getByRole("button",{name:"Manage projects"}).click();
      await page.getByRole("button",{name:"Delete project…",exact:true}).click();
      await page.getByRole("button",{name:"Move to trash"}).click();
      await page.waitForURL(url);
      await page.getByRole("button",{name:"Manage projects"}).click();
      await page.getByRole("button",{name:"View trash"}).click();
      await page.getByRole("button",{name:"Restore"}).click();
      await page.getByText("Trash is empty.").waitFor();
      await page.keyboard.press("Escape");
      await page.getByLabel("Project",{exact:true}).selectOption(path);
      await page.getByRole("navigation",{name:"Sessions"}).getByRole("button",{name:"Local session",exact:true}).click();
      await page.getByRole("heading",{name:"Local session"}).waitFor();
      await page.getByRole("button",{name:"MCP connectors",exact:true}).click();
      const sources = page.getByRole("region",{name:"Built-in public databases"});
      await sources.getByText("Blocked",{exact:true}).waitFor();
      await page.mouse.click(2,2);
      await page.locator("#workbench-settings").waitFor({state:"hidden"});
      await projectSettings.click();
      await page.getByLabel("Data classification").selectOption("public");
      await page.mouse.click(2,2);
      await page.locator("#settings-dialog").waitFor({state:"hidden"});
      await projectSettings.click();
      assert.equal(await page.getByLabel("Data classification").inputValue(),"internal");
      await page.getByLabel("Data classification").selectOption("public");
      await page.getByRole("button",{name:"Save settings"}).click();
      await page.locator("#settings-dialog").waitFor({state:"hidden"});
      await page.getByRole("button",{name:"MCP connectors",exact:true}).click();
      await sources.getByRole("button",{name:"Disable public search"}).waitFor();
      await page.getByRole("button",{name:"Close workbench settings"}).click();
      await page.getByRole("button",{name:"New project",exact:true}).click();
      await page.getByLabel("Project name").fill("Public search fixture");
      await page.getByRole("combobox",{name:"Data classification"}).selectOption("public");
      await page.getByRole("button",{name:"Create project"}).click();
      await page.waitForURL(u=>u.pathname.startsWith("/projects/") && u.pathname!==path);
      await page.getByRole("button",{name:"MCP connectors",exact:true}).click();
      await sources.getByRole("button",{name:"Disable public search"}).click();
      await sources.getByRole("button",{name:"Enable public search"}).click();
      await sources.getByText("Enabled",{exact:true}).waitFor();
      await page.keyboard.press("Escape");
      await page.getByRole("button",{name:"Chat settings"}).click();
      const chat = page.getByRole("dialog",{name:"Chat settings"});
      await chat.getByLabel("Literature subtasks").check();
      await chat.getByRole("button",{name:"Use public literature tools"}).click();
      await chat.getByRole("button",{name:"Save settings"}).click();
      await chat.waitFor({state:"hidden"});
      await page.getByLabel("Dedicated literature lookup").check();
      assert.ok(await page.getByLabel("Review a plan first").isDisabled());
      await page.getByLabel("Research question").fill("Which publications address this question?");
      let sent;
      await page.route("**/api/sessions/*/turns",async route=>{
        sent=route.request().postDataJSON();
        await route.fulfill({status:400,json:{detail:"Captured offline"}});
      });
      await page.getByRole("button",{name:"Run research"}).click();
      await page.getByText("Captured offline").waitFor();
      assert.equal(sent.question,"/literature Which publications address this question?");
      assert.equal(sent.steps,null);
      assert.deepEqual(errors,[]);console.log("Project lifecycle, restricted search status and Public database controls passed");return;
    }
    if (process.env.BQ_TEST_LAYOUT) {
      const settings=page.getByRole("button",{name:"Project settings",exact:true});
      await settings.waitFor();
      for(let i=0;i<60 && !await settings.evaluate(n=>n===document.activeElement);i++) await page.keyboard.press("Tab");
      assert.ok(await settings.evaluate(n=>n===document.activeElement),"keyboard reaches settings");
      await page.keyboard.press("Enter");
      await page.getByRole("dialog",{name:"Project settings"}).waitFor();
      await page.keyboard.press("Escape");
      assert.ok(await settings.evaluate(n=>n===document.activeElement),"Escape restores focus");
      const sidebar=await page.getByRole("complementary",{name:"Project and sessions"}).boundingBox();
      const main=await page.getByRole("main").boundingBox();
      assert.ok(sidebar.x+sidebar.width<=main.x,"session navigation is left of the record");
      await page.getByRole("button",{name:"Try the metformin example"}).click();
      await page.getByRole("button",{name:"Submit plan"}).click();
      await page.getByRole("button",{name:"Approve and run"}).click();
      await page.getByRole("heading",{name:"Findings"}).waitFor();
      for (const [side,selector,delta] of [["left",".sidebar",48],["right","#research-panels",-48]]) {
        const handle = page.getByRole("separator",{name:"Resize "+side+" sidebar"});
        const initial = (await page.locator(selector).boundingBox()).width;
        const grip = await handle.boundingBox();
        assert.ok(grip,side+" resize handle is visible");
        await page.mouse.move(grip.x+grip.width/2,grip.y+70);
        await page.mouse.down();await page.mouse.move(grip.x+grip.width/2+delta,grip.y+70);await page.mouse.up();
        assert.ok((await page.locator(selector).boundingBox()).width>=initial+40);
        await handle.focus();await page.keyboard.press(delta>0?"ArrowLeft":"ArrowRight");
        assert.ok((await page.locator(selector).boundingBox()).width<initial+40);
      }
      await page.getByRole("button",{name:"Collapse navigation"}).click();
      assert.ok((await page.locator(".sidebar").boundingBox()).width<80);
      await page.getByRole("button",{name:"Expand navigation"}).click();
      const inspector=page.getByRole("navigation",{name:"Research panels"});
      for(const name of ["Evidence","Artifacts","Activity","Usage","Context"]) {
        await inspector.getByRole("button",{name,exact:true}).click();
        assert.equal(await inspector.getByRole("button",{name,exact:true}).getAttribute("aria-pressed"),"true");
      }
      await page.getByRole("button",{name:"Hide research panels"}).click();
      await page.getByRole("button",{name:"Show research panels"}).click();
      const ratios=await page.evaluate(()=>{
        function rgb(el,property) {return getComputedStyle(el)[property].match(/[\d.]+/g).map(Number);}
        function light(c){const a=c.slice(0,3).map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);return a[0]*.2126+a[1]*.7152+a[2]*.0722;}
        return [".project-name",".muted",".panel-tabs [aria-pressed=true]",".badge",".evidence-link"].map(selector=>{
          const el=document.querySelector(selector); const fg=light(rgb(el,"color"));let bg=el;
          while(bg.parentElement && rgb(bg,"backgroundColor")[3]===0) bg=bg.parentElement;
          const back=light(rgb(bg,"backgroundColor"));return [selector,(Math.max(fg,back)+.05)/(Math.min(fg,back)+.05)];
        });
      });
      for(const [selector,ratio] of ratios) assert.ok(ratio>=4.5,selector+" contrast "+ratio);
      await inspector.getByRole("button",{name:"Usage",exact:true}).click();
      await page.getByText("Completed model calls").waitFor();
      for(const width of [1440,1024,768,390,320]){
        await page.setViewportSize({width,height:844});
        assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),"overflow at "+width);
        assert.ok(await page.locator(".metric-list dd").first().evaluate(el => {
          const range = document.createRange(); range.selectNodeContents(el);
          return range.getClientRects().length === 1 && el.scrollWidth <= el.clientWidth;
        }), "usage value wraps at "+width);
      }
      assert.deepEqual(errors,[]);console.log("Keyboard, five panel selectors, responsive widths and contrast",ratios);return;
    }
    if (process.env.BQ_TEST_FIRST_RUN) {
      await page.getByText("No research model selected").waitFor();
      await page.getByText(/Kernel unavailable. Python calculations cannot run/).waitFor();
      await page.getByRole("button",{name:"Project settings",exact:true}).click();
      const settings=page.getByRole("dialog",{name:"Project settings"});
      await settings.getByText("Connectors",{exact:true}).click();
      await settings.getByRole("button",{name:"Reconnect"}).click();
      await settings.getByText(/library .*unavailable/).waitFor();
      await settings.getByRole("button",{name:"Close settings"}).click();
      await page.reload();
      await page.getByText(/MCP connector library is disconnected/).waitFor();
      await page.getByRole("link",{name:"Open the offline metformin example"}).click();
      await page.getByRole("button",{name:"Try the metformin example"}).click();
      await page.getByRole("button",{name:"Submit plan"}).click();
      await page.getByRole("button",{name:"Approve and run"}).click();
      await page.getByRole("heading",{name:"Findings"}).waitFor();
      const primary = await (await page.request.get(url+"api/sessions")).json();
      assert.deepEqual(primary.sessions.map(s=>s.title),["Initial session"]);
      console.log("Missing worker, unavailable kernel, disconnected MCP and isolated offline example verified");return;
    }
    if (process.env.BQ_TEST_UPLOAD) {
      const button=name=>page.getByRole("button",{name,exact:true});
      await button("New session").click();
      await page.getByLabel("Session title").fill("Acceptance walk-through");
      await button("Continue").click();
      await page.getByRole("heading",{name:"Acceptance walk-through"}).waitFor();
      await page.getByRole("combobox",{name:"Worker model"}).selectOption("openai:fixture");
      const send=async q=>{await page.getByLabel("Research question").fill(q);await button("Run research").click();};
      await send("Calculate the initial count.");
      await button("5").first().waitFor();
      await send("Describe the groups.");
      await page.getByText("The initial calculation used two groups.",{exact:true}).first().waitFor();
      await button("5").first().click();
      await button("Quote value in question").click();
      await send("Explain the selected count.");
      await page.getByText("The selected calculation summed the two groups.",{exact:true}).first().waitFor();
      await sessionAction(page, "Model usage");
      await page.getByRole("dialog").getByText("Completed model calls").waitFor();
      await button("Close model usage").click();
      await sessionAction(page, "Model context");
      await button("Compact now").click();
      await page.getByRole("dialog").getByText(/Events .*openai:fixture/).waitFor();
      await button("Close context").click();
      await button("Project settings").click();
      const settings=page.getByRole("dialog",{name:"Project settings"});
      await settings.getByRole("checkbox",{name:"Literature subtasks"}).check();
      await button("Save settings").click();
      await settings.waitFor({state:"hidden"});
      await page.locator("#composer").getByRole("button",{name:"Attach a file"}).click();
      await page.getByRole("dialog",{name:"Attach a file"}).waitFor();
      const bytes = Buffer.from("sample,count\na,3\nb,4\n");
      const digest = require("node:crypto").createHash("sha256").update(bytes).digest("hex");
      await page.getByLabel("Upload file").setInputFiles({name:"counts.csv",mimeType:"text/csv",buffer:bytes});
      await page.getByRole("dialog",{name:"Attach a file"}).getByRole("button",{name:"Attach file",exact:true}).click();
      await page.getByRole("list",{name:"Attached files"}).getByText("counts.csv").waitFor();
      await page.getByLabel("Research question").fill("Sum the count column in the attached file.");
      await page.getByRole("button",{name:"Run research"}).click();
      await page.getByRole("button",{name:"7",exact:true}).first().click({timeout:60000});
      await page.getByRole("dialog").getByText(digest,{exact:false}).first().waitFor();
      await page.getByRole("button",{name:"Close evidence"}).click();
      await page.getByRole("button",{name:"Artifacts",exact:true}).click();
      await page.locator(".artifact-row").filter({hasText:"counts.csv"}).getByRole("button",{name:"Preview table"}).click();
      await page.getByRole("cell",{name:"4",exact:true}).waitFor();
      await page.getByRole("button",{name:"Close preview"}).click();
      await page.getByLabel("Research question").fill("Keep my follow-up draft");
      await page.locator("#record").getByRole("button",{name:"Open summary.csv"}).click();
      const viewer = page.getByRole("region",{name:"Result viewer"});
      await viewer.getByRole("cell",{name:"7",exact:true}).waitFor();
      await page.locator("#record").getByRole("button",{name:"Open report.md"}).click();
      await viewer.getByRole("heading",{name:"Count report"}).waitFor();
      await viewer.getByRole("cell",{name:"7",exact:true}).waitFor();
      await viewer.getByRole("img",{name:"Counts by sample"}).waitFor();
      await page.waitForFunction(()=>document.querySelector(".report-figure img")?.naturalWidth>0);
      if(process.env.BQ_RESULT_SCREENSHOT) {
        await page.getByRole("button",{name:"Collapse navigation"}).click();
        await page.screenshot({path:process.env.BQ_RESULT_SCREENSHOT});
      }
      await page.setViewportSize({width:390,height:844});
      assert.ok(await viewer.evaluate(el=>el.clientWidth<=innerWidth));
      await viewer.getByRole("button",{name:"summary.csv",exact:true}).click();
      await page.setViewportSize({width:1440,height:900});
      assert.equal(await page.getByLabel("Research question").inputValue(),"Keep my follow-up draft");
      await viewer.getByRole("button",{name:"Close preview"}).click();
      await viewer.getByRole("button",{name:"Close preview"}).click();
      const response=await page.request.get(await page.getByText("Download kernel notebook").getAttribute("href").then(p => new URL(p,page.url()).href));
      assert.match((await response.json()).cells.at(-1).source.join(""), /csv.DictReader/);
      await sessionAction(page, "Export research bundle");
      const received = page.waitForEvent("download");
      await page.getByRole("link",{name:"Download research bundle"}).click();
      assert.match((await received).suggestedFilename(),/\.zip$/);
      console.log("Uploaded CSV, real kernel result, input hash, table and notebook verified");return;
    }
    if (process.env.BQ_TEST_REFERENCES) {
      await page.getByRole("button", {name:"23", exact:true}).click();
      await page.getByRole("button", {name:"Quote value in question"}).click();
      await page.getByRole("list", {name:"References"}).getByText(/eligible/).waitFor();
      await page.getByLabel("Research question").fill("这个数是怎么算的？");
      await page.getByRole("button", {name:"Run research"}).click();
      await page.getByText("The recorded code adds 17 and 6 eligible samples.", {exact:true}).first().waitFor();
      console.log("Follow-up matched the cassette for the original code and declared value");
      return;
    }
    if (process.env.BQ_TEST_CONVERSATION) {
      await page.getByRole("button", {name: "Project settings", exact: true}).click();
      const settings = page.getByRole("dialog", {name: "Project settings"});
      await settings.getByText("Runtime and project", {exact:true}).click();
      await settings.getByText(/R packages \(/).waitFor();
      await settings.getByText(/pixi add r-base r-irkernel/).waitFor();
      const skills = settings.getByRole("checkbox", {name: "Research skills", exact: true});
      await skills.check();
      await settings.getByRole("button", {name: "Save settings"}).click();
      await settings.waitFor({state:"hidden"});
      await page.reload();
      await page.getByRole("button", {name: "Project settings", exact: true}).click();
      assert.ok(await skills.isChecked());
      await settings.getByRole("button", {name:"Close settings"}).click();
      await verifyConversation(page);
      assert.deepEqual(errors, []);
      return;
    }
    if (process.env.BQ_TEST_PROJECT_NOTE) {
      await verifyProjectNote(page);
      assert.deepEqual(errors, []);
      return;
    }
    if (process.env.BQ_TEST_REAL_PROJECT) {
      await verifyRealProject(page);
      assert.deepEqual(errors, []);
      return;
    }
    await page.getByRole("button", {name: "Try the metformin example"}).click();
    await page.getByRole("button", {name: "Submit plan"}).click();
    await page.getByLabel("Research record").getByText("Awaiting approval", {exact: true}).waitFor();
    await page.getByLabel("Research activity").getByText("Awaiting approval", {exact: true}).waitFor();
    assert.equal(await page.getByRole("heading", {name: "Findings"}).count(), 0);
    await page.reload();
    await page.getByRole("button", {name: "Approve and run"}).click();
    await page.getByRole("heading", {name: "Findings"}).waitFor();
    await page.getByText("Reviewed", {exact: true}).first().waitFor();
    await page.getByText("Assertion only", {exact: true}).waitFor();
    await sessionAction(page, "Audit log");
    const journal = page.getByRole("dialog", {name: "Audit log"});
    await journal.getByText(/Data classification/).waitFor();
    await journal.getByText("model call started", {exact: true}).first().waitFor();
    await journal.getByRole("button", {name: "Close audit log"}).click();
    await sessionAction(page, "Model usage");
    const usage = page.getByRole("dialog", {name: "Model usage"});
    await usage.getByLabel("Scope").selectOption("session");
    await usage.getByText("worker", {exact: true}).waitFor();
    await usage.getByRole("button", {name: "Close model usage"}).click();
    const figure = page.getByRole("img", {name: "Figure from the recorded calculation"});
    await figure.evaluate((image) => image.scrollIntoView({block: "center"}));
    await page.waitForFunction(() => document.querySelector(".figure img")?.naturalWidth > 0);
    await page.getByRole("button",{name:"Hide research panels"}).click();
    await figure.click();
    const reader=page.getByRole("region",{name:"Result viewer"});
    await reader.getByRole("img").waitFor();
    assert.equal(await page.locator("dialog[open]").count(),0);
    assert.ok((await reader.boundingBox()).x>=(await page.locator("#workspace").boundingBox()).x+(await page.locator("#workspace").boundingBox()).width-1);
    await reader.getByRole("button",{name:"Close preview"}).click();
    await page.getByRole("button", {name: "3", exact: true}).click();
    let drawer = page.getByRole("dialog", {name: "excerpts"});
    await drawer.getByRole("heading", {name: "Input hashes"}).waitFor();
    assert.match(await drawer.innerText(), /value\("excerpts"/);
    assert.match(await drawer.innerText(), /[0-9a-f]{64}/);
    await drawer.getByRole("button", {name: "Close evidence"}).click();
    await page.getByRole("button", {name: "[PMID:11118008]", exact: true}).click();
    drawer = page.getByRole("dialog", {name: "PMID:11118008"});
    assert.equal(await drawer.locator("mark").innerText(), "through a reduction in gluconeogenesis");
    await drawer.getByRole("button", {name: "Close evidence"}).click();
    await sessionAction(page, "Fork session");
    await page.getByLabel("Session title").fill("Compare a narrower set");
    await page.getByRole("button", {name: "Continue", exact: true}).click();
    await page.getByRole("heading", {name: "Compare a narrower set"}).waitFor();
    await page.reload();
    await page.getByRole("button", {name: "[PMID:11118008]", exact: true}).click();
    await page.getByRole("dialog", {name: "PMID:11118008"}).getByRole("button", {name: "Close evidence"}).click();
    if (process.env.BQ_TEST_SCREENSHOT) await page.screenshot({path: process.env.BQ_TEST_SCREENSHOT});
    await page.setViewportSize({width: 390, height: 844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.getByRole("button", {name: "Projects and sessions"}).click();
    await page.getByRole("navigation", {name: "Sessions"}).getByRole("button", {name: "Compare a narrower set"}).click();
    await page.waitForFunction(() => document.activeElement === document.getElementById("session-name"));
    assert.deepEqual(errors, []);
    console.log("Workbench browser flow passed");
  } finally {
    await browser?.close();
    if (server.exitCode === null) {
      const exited = once(server, "exit");
      server.kill("SIGTERM");
      await exited;
    }
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
