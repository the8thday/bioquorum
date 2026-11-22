const assert = require("node:assert/strict");
const {readFile} = require("node:fs/promises");

module.exports = async function verifyHer2(page) {
  page.setDefaultTimeout(20000);
  const button = name => page.getByRole("button", {name, exact:true});
  const viewer = page.getByRole("region", {name:"Result viewer"});
  await button("[PMID:12610629]").first().click();
  const evidence = page.getByRole("dialog", {name:"PMID:12610629"});
  assert.match(await evidence.locator("mark").innerText(), /juxtamembrane region/);
  await button("Close evidence").click();
  await button("Open interface.fasta").click();
  await viewer.getByRole("button", {name:/Interface · A ASN 30/}).click();
  assert.equal(await viewer.getByLabel("Start", {exact:true}).inputValue(), "30");
  await button("Quote range").click();
  await button("Open 1N8Z.cif").click();
  await viewer.getByLabel("Residue",{exact:true}).selectOption({label:"C · ASP 596"});
  await button("Quote selection").click();
  assert.equal(await page.getByRole("list",{name:"References"}).getByRole("button",{name:"Remove reference"}).count(),2);
  await button("Close preview").click();
  await button("Research lines").click();
  const downloading = page.waitForEvent("download");
  await page.getByRole("link",{name:"Download handoff summary"}).click();
  const handoff = JSON.parse(await readFile(await (await downloading).path(),"utf8"));
  assert.equal(handoff.materials.length,3);
  assert.equal(handoff.claims.length,2);
  assert.ok(handoff.claims.some(c => c.text.includes("32 distinct residue pairs")));
  console.log("1N8Z sequence annotations, structure selection, paper excerpt and research-line handoff passed");
};
