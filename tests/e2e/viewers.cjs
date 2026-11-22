const assert = require("node:assert/strict");

module.exports=async function verifyViewers(page) {
  page.setDefaultTimeout(15000);
  page.on("pageerror", e=>console.error(e.message));
  const remote=[];
  page.on("request", r=>{if(/^https?:/.test(r.url()) && new URL(r.url()).origin!==new URL(page.url()).origin) remote.push(r.url());});
  const button=(name)=>page.getByRole("button",{name,exact:true});
  const viewer=page.getByRole("region",{name:"Result viewer"});
  await page.getByRole("heading",{name:"Upload fixture"}).waitFor();
  const base=new URL(page.url()).origin+"/api/sessions/"+new URL(page.url()).searchParams.get("session");
  async function upload(name,raw) {
    const result=await page.request.post(base+"/artifacts?classification=public&name="+encodeURIComponent(name),
      {headers:{"Content-Type":"application/octet-stream", Origin:new URL(page.url()).origin},data:Buffer.from(raw)});
    assert.equal(result.status(),201,await result.text());
    await page.reload();
  }
  async function send(q) {
    await page.getByLabel("Research question").fill(q);
    await button("Run research").click();
  }
  await upload("unknown.xyz0","test");
  await button("Artifacts").click();
  const unknown=page.locator(".artifact-row").filter({hasText:"unknown.xyz0"});
  assert.equal(await unknown.getByRole("link",{name:"Download"}).count(),1);
  assert.equal(await unknown.getByRole("button",{name:/preview|view/i}).count(),0);
  const pdb="ATOM      1  N   TYR H  52A      1.000   1.000   1.000  1.00 20.00           N  \n"+
    "ATOM      2  CA  TYR H  52A      2.400   1.000   1.000  1.00 20.00           C  \n"+
    "ATOM      3  C   TYR H  52A      3.100   2.100   1.000  1.00 20.00           C  \nEND\n";
  await upload("mini.pdb",pdb);
  await button("Open mini.pdb").click();
  await viewer.getByLabel("Residue",{exact:true}).selectOption({label:"H · TYR 52A"}).catch(async e=>{console.error(await viewer.innerText());throw e;});
  assert.equal(await viewer.locator("canvas").count(),1);
  await button("Quote selection").click();
  await send("Identify my structure selection");
  await page.getByText("Selected author residue H 52A.",{exact:true}).first().waitFor();
  await page.reload();
  await button("Reopen structure selection").click();
  await page.waitForFunction(()=>document.querySelector(".result-viewer select")?.selectedOptions[0]?.textContent==="H · TYR 52A");
  const fields="group_PDB id type_symbol label_atom_id label_alt_id label_comp_id label_asym_id label_entity_id label_seq_id pdbx_PDB_ins_code Cartn_x Cartn_y Cartn_z occupancy B_iso_or_equiv auth_seq_id auth_comp_id auth_asym_id auth_atom_id pdbx_PDB_model_num".split(" ");
  await upload("alternate.cif","data_test\nloop_\n"+fields.map(f=>"_atom_site."+f).join("\n")+"\n"+
    "ATOM 1 C CA . TYR LA 1 90 A 0 0 0 1 20 52 TYR H CA 7\n"+
    "ATOM 2 N N . TYR LA 1 90 A 1 0 0 1 20 52 TYR H N 7\n#\n");
  await button("Open alternate.cif").click();
  await viewer.getByLabel("Residue",{exact:true}).selectOption({label:"H · TYR 52A"});
  await viewer.getByLabel("Selection",{exact:true}).selectOption("chain");
  await button("Quote selection").click();
  await page.getByRole("list",{name:"References"}).getByText("alternate.cif · chain H").waitFor();
  await button("Remove reference").click();
  await upload("sequences.faa",">sample\nMLPK\n");
  await button("Open sequences.faa").click();
  await viewer.getByLabel("Start",{exact:true}).fill("2");
  await viewer.getByLabel("End",{exact:true}).fill("3");
  await button("Quote range").click();
  await page.getByRole("list",{name:"References"}).getByText("sample · 2–3").waitFor();
  await button("Remove reference").click();
  const sdf="ethanol\n  BQ\n\n  3  2  0  0  0  0  0  0  0  0999 V2000\n"+
    "    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"+
    "    1.3000    0.7500    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"+
    "    2.6000    0.0000    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0\n"+
    "  1  2  1  0\n  2  3  1  0\nM  END\n$$$$\n";
  await upload("molecule.sdf",sdf);
  await button("Open molecule.sdf").click();
  await button("Quote molecule").click().catch(async e=>{console.error(await viewer.innerText());throw e;});
  await page.waitForFunction(()=>document.querySelector("img.molecule-canvas")?.naturalWidth>0);
  await button("Remove reference").click();
  await upload("intervals.bed","chrTest\t1\t25\nchrTest\t38\t78\n");
  await button("Open intervals.bed").click();
  await viewer.getByLabel("Interval",{exact:true}).selectOption("1").catch(async e=>{console.error(await viewer.innerText());throw e;});
  await button("Quote interval").click();
  await page.getByRole("list",{name:"References"}).getByText("intervals.bed · chrTest [38, 78)").waitFor();
  await button("Remove reference").click();
  await upload("counts.csv","sample,count\na,3\nb,4\n");
  await button("Open counts.csv").click();
  await viewer.getByLabel("Sort",{exact:true}).selectOption("1");
  await viewer.getByRole("button",{name:"Ascending"}).click();
  await viewer.getByRole("button",{name:"4",exact:true}).click();
  await page.getByRole("list",{name:"References"}).getByText("counts.csv · row 2 · count").waitFor();
  await button("Remove reference").click();
  await button("Close preview").click();
  await button("Artifacts").click();
  await page.locator(".artifact-row").filter({hasText:"counts.csv"}).getByRole("button",{name:"Attach to question"}).click();
  await send("Sum the count column in the attached file.");
  await button("Open report.md").click({timeout:60000});
  await viewer.getByText("Select report lines").click();
  await viewer.getByLabel("First line").fill("3");
  await viewer.getByLabel("Last line").fill("3");
  await viewer.getByRole("button",{name:"Quote lines"}).click();
  await page.getByRole("list",{name:"References"}).getByText("report.md · lines 3–3").waitFor();
  await button("Remove reference").click();
  await button("Open marked.faa").click({timeout:60000});
  await viewer.getByRole("button",{name:"mapped segment · 2–3"}).click();
  await button("Quote range").click();
  await page.getByRole("list",{name:"References"}).getByText("fixture · 2–3").waitFor();
  await button("Remove reference").click();
  await button("Open counts.svg").click({timeout:60000});
  const overlay=viewer.getByLabel("Select plotted samples");
  await overlay.waitFor();
  await overlay.locator('circle[data-point="0"]').click();
  await button("Quote selected samples (1)").click();
  await page.getByRole("list",{name:"References"}).getByText("counts.svg · a",{exact:true}).waitFor();
  await button("Remove reference").click();
  await overlay.click({position:{x:2,y:2}});
  assert.ok(await button("Quote selected samples (0)").isDisabled());
  const rect=await overlay.boundingBox();
  await page.mouse.move(rect.x+rect.width*.55,rect.y+rect.height*.55);
  await page.mouse.down();
  await page.mouse.move(rect.x+rect.width*.85,rect.y+rect.height*.85);
  await page.mouse.up();
  await button("Quote selected samples (1)").click();
  await send("Recalculate after excluding my selected sample");
  await button("Open recomputed.svg").waitFor();
  await page.waitForFunction(()=>document.querySelector('select[aria-label="Compare with"]')?.selectedOptions[0]?.textContent==="recomputed.svg · v1");
  await viewer.getByRole("img",{name:"Comparison figure"}).waitFor();
  assert.equal(await viewer.locator(".figure-pair img").count(),2);
  assert.deepEqual(remote,[], "Viewers use only local assets and data");
  console.log("Offline figure recomputation, table, sequence, 3D picking and replay passed");
};
