import {useEffect, useRef, useState} from "react";
import {OrderedSet} from "molstar/lib/mol-data/int";
import {StructureElement, StructureProperties as P, Unit, type Structure} from "molstar/lib/mol-model/structure";
import {PluginContext} from "molstar/lib/mol-plugin/context";
import {setStructureTransparency,clearStructureTransparency} from "molstar/lib/mol-plugin-state/helpers/structure-transparency";
import {DefaultPluginSpec} from "molstar/lib/mol-plugin/spec";
import {filePath, type ViewerProps} from "./registry";
import type {StructurePick} from "../types";

type Site = {pick:StructurePick; label:string; loci:StructureElement.Loci};

function indexedSites(structure:Structure) {
  const sites=new Map<string,Site>();
  for(const unit of structure.units) {
    if(!Unit.isAtomic(unit)) continue;
    let previous=-1;
    for(let index=0;index<unit.elements.length;index++) {
      const element=unit.elements[index];
      const residue=unit.residueIndex[element];
      if(previous===residue) continue;
      previous=residue;
      const loc=StructureElement.Location.create(structure,unit,element);
      const pick:StructurePick={type:"structure", model:unit.model.modelNum,
        chain:P.chain.auth_asym_id(loc),label_chain:P.chain.label_asym_id(loc),
        residue:P.residue.auth_seq_id(loc),label_residue:P.residue.label_seq_id(loc),
        insertion:P.residue.pdbx_PDB_ins_code(loc)};
      const loci=StructureElement.Loci(structure,[{unit,indices:OrderedSet.ofSingleton(index as UnitIndex)}]);
      const label=(pick.chain || "(blank chain)")+" · "+P.atom.label_comp_id(loc)+" "+pick.residue+(pick.insertion || "");
      sites.set(unit.id+"/"+residue,{pick,label,loci});
    }
  }
  return sites;
}
type UnitIndex = Parameters<typeof StructureElement.Loci>[1][number]["indices"] extends OrderedSet<infer T> ? T : never;

export default function StructureViewer({file,base,quote,initial}:ViewerProps) {
  const host=useRef<HTMLDivElement>(null);
  const plugin=useRef<PluginContext|null>(null);
  const [sites,setSites]=useState(new Map<string,Site>());
  const [key,setKey]=useState("");
  const [granularity,setGranularity]=useState<"chain"|"residue">(initial?.type==="structure" && initial.residue==null?"chain":"residue");
  const [error,setError]=useState("");
  const site=sites.get(key);
  const [visibilityBusy,setVisibilityBusy]=useState(false);
  async function visibility(reset:boolean) {
    const ctx=plugin.current;
    if(!ctx || !site || visibilityBusy)return;
    setVisibilityBusy(true);
    const components=ctx.managers.structure.hierarchy.current.structures.flatMap(s=>s.components);
    try {
      if(reset)await clearStructureTransparency(ctx,components);
      else {
        const loci=StructureElement.Loci.extendToWholeChains(site.loci);
        await setStructureTransparency(ctx,components,1,async structure=>StructureElement.Loci.remap(loci,structure));
      }
    } catch(e){setError(String(e));}
    finally{setVisibilityBusy(false);}
  }
  useEffect(()=>{
    const controller=new AbortController();
    const ctx=new PluginContext(DefaultPluginSpec());
    let mounted=false;
    let resize:ResizeObserver|undefined;
    async function load() {
      await ctx.init();
      if(controller.signal.aborted || !host.current) return;
      if(!await ctx.mountAsync(host.current)) throw Error("WebGL could not initialize. Download the structure to open it locally.");
      mounted=true;
      resize=new ResizeObserver(()=>ctx.handleResize());resize.observe(host.current);
      const response=await fetch(filePath(base,file.id)+"/file",{signal:controller.signal});
      if(!response.ok) throw Error("Recorded structure unavailable");
      const data=await ctx.builders.data.rawData({data:await response.text(),label:file.name});
      const format=/\.(pdb|ent)$/i.test(file.name)?"pdb":"mmcif";
      const trajectory=await ctx.builders.structure.parseTrajectory(data,format);
      const model=await ctx.builders.structure.createModel(trajectory);
      const structure=await ctx.builders.structure.createStructure(model,{name:"model",params:{}});
      if(!structure.obj) throw Error("The file contains no coordinate model");
      // Small fragments may lack a polymer trace; show their actual atomic geometry.
      if(structure.obj.data.elementCount>1000) {
        await ctx.builders.structure.representation.applyPreset(structure,"auto",{theme:{globalName:"chain-id"}});
      } else {
        const component=await ctx.builders.structure.tryCreateComponentStatic(structure,"all");
        if(!component) throw Error("No atoms available for display");
        await ctx.builders.structure.representation.addRepresentation(component,{type:"ball-and-stick",color:"chain-id"});
        const polymer=await ctx.builders.structure.tryCreateComponentStatic(structure,"polymer");
        if(polymer) await ctx.builders.structure.representation.addRepresentation(polymer,{type:"cartoon",color:"chain-id"});
      }
      if(controller.signal.aborted) return;
      const records=indexedSites(structure.obj.data);
      if(!records.size) throw Error("No selectable atomic residues");
      plugin.current=ctx;setSites(records);
      if(initial?.type==="structure") {
        const entry=[...records].find(([,s])=>s.pick.model===initial.model && s.pick.label_chain===initial.label_chain &&
          (initial.residue==null || (s.pick.residue===initial.residue && (initial.label_residue==null || initial.label_residue===s.pick.label_residue) && s.pick.insertion===(initial.insertion??""))));
        if(entry) setKey(entry[0]); else setError("The saved selection is unavailable in this coordinate model.");
      }
      ctx.behaviors.interaction.click.subscribe(({current})=>{
        if(!StructureElement.Loci.is(current.loci)) return;
        const loc=StructureElement.Loci.getFirstLocation(current.loci);
        if(loc && Unit.isAtomic(loc.unit)) setKey(loc.unit.id+"/"+loc.unit.residueIndex[loc.element]);
      });
      ctx.canvas3d?.requestCameraReset();
    }
    const running=load().catch(e=>{if(!controller.signal.aborted)setError(String(e));});
    return ()=>{controller.abort();resize?.disconnect();plugin.current=null;if(mounted)ctx.unmount();void running.finally(()=>ctx.dispose());};
  },[base,file.id,file.name,initial]);
  useEffect(()=>{
    const ctx=plugin.current;
    if(!ctx || !site) return;
    const loci=granularity==="chain"?StructureElement.Loci.extendToWholeChains(site.loci):StructureElement.Loci.extendToWholeResidues(site.loci);
    ctx.managers.interactivity.lociSelects.deselectAll();
    ctx.managers.interactivity.lociSelects.select({loci},false);
    ctx.managers.camera.focusLoci(loci);
  },[site,granularity]);
  function cite() {
    if(!site || !quote) return;
    const {model,chain,label_chain}=site.pick;
    const selection:StructurePick=granularity==="residue"?site.pick:{type:"structure",model,chain,label_chain};
    quote({kind:"artifact",id:file.id,selection},file.name+" · "+(granularity==="chain"?"chain "+chain:site.label));
  }
  return <><div className="structure-canvas" aria-label="Molecular structure" ref={host}/>
    {error ? <p role="alert">{error}</p> : sites.size===0 ? <p role="status">Loading structure…</p> :
      <div className="viewer-controls"><label>Residue <select aria-label="Residue" value={key} onChange={e=>setKey(e.target.value)}>
        <option value="">Pick a residue</option>{[...sites].map(([id,s])=><option key={id} value={id}>{s.label}</option>)}</select></label>
        <label>Selection <select aria-label="Selection" value={granularity} onChange={e=>setGranularity(e.target.value as typeof granularity)}><option value="residue">Residue</option><option value="chain">Chain</option></select></label>
        <button className="quiet" disabled={!site || visibilityBusy} onClick={()=>void visibility(false)}>Hide chain</button>
        <button className="quiet" disabled={!site || visibilityBusy} onClick={()=>void visibility(true)}>Show all chains</button>
        {quote && <button className="quiet" disabled={!site} onClick={cite}>Quote selection</button>}</div>}</>;
}
