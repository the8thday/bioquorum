import initRDKit from "@rdkit/rdkit";

let library:ReturnType<typeof initRDKit>|undefined;
self.onmessage=async (event:MessageEvent<{bytes?:ArrayBuffer;record:string;index:number}>)=>{
  const {bytes,record,index}=event.data;
  try {
    if(!library) {
      if(!bytes) throw Error("Missing RDKit runtime");
      const module=await WebAssembly.compile(bytes);
      library=initRDKit({instantiateWasm:(imports:WebAssembly.Imports,ready:(i:WebAssembly.Instance)=>void)=>{
        const instance=new WebAssembly.Instance(module,imports);ready(instance);return instance.exports;
      }});
    }
    const rd=await library;
    const molecule=rd.get_mol(record);
    if(!molecule)throw Error("RDKit could not parse this molecule");
    try {self.postMessage({index,svg:molecule.get_svg(480,320)});}
    finally {molecule.delete();}
  } catch {
    self.postMessage({index,error:"RDKit could not render this molecule."});
  }
};
