import {lazy, type ComponentType} from "react";
import type {Product} from "../Artifacts";
import type {TurnRef} from "../types";

export interface ViewerProps {
  file:Product;
  comparedFigure?:string;
  initial?:TurnRef["selection"];
  base:string;
  products:Product[];
  open:(p:Product)=>void;
  quote?:(item:TurnRef, label:string)=>void;
}

type Module = {default:ComponentType<ViewerProps>};
function viewer(load:()=>Promise<Module>) {
  return {load, Component:lazy(load)};
}
const registry = new Map([
  ["genome", viewer(()=>import("./Genome"))],
  ["molecule", viewer(()=>import("./Molecule"))],
  ["sequence", viewer(()=>import("./Sequence"))],
  ["structure", viewer(()=>import("./Structure"))],
  ["table", viewer(()=>import("./Table"))],
  ["figure", viewer(()=>import("./Figure"))],
  ["report", viewer(()=>import("./Report"))],
  ["html", viewer(()=>import("./Html"))],
]);
export const viewerFor = (type:string|null) => type ? registry.get(type) : undefined;
export const filePath = (base:string, id:string) => base+"/artifacts/"+encodeURIComponent(id);
