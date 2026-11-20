import { useEffect, useRef, useState, type CSSProperties } from "react";

const clamp = (n:number,min:number,max:number) => Math.max(min,Math.min(max,n));

export function useSidebarLayout() {
  const [width,setWidth] = useState(window.innerWidth);
  const [left,setLeft] = useState<number>();
  const [inspector,setInspector] = useState<number>();
  const [reader,setReader] = useState<number>();
  const [compact,setCompact] = useState(false);
  useEffect(()=>{
    const resize = ()=>setWidth(window.innerWidth);
    window.addEventListener("resize",resize);
    return ()=>window.removeEventListener("resize",resize);
  },[]);
  function columns(viewing:boolean,rightOpen:boolean) {
    const leftMax = Math.min(440,width-340-(rightOpen && width>=1000 ? 280 : 0));
    const leftWidth = compact ? 54 : clamp(left ?? width*.17,180,leftMax);
    const rightMax = width>=1000 ? width-leftWidth-340 : width-80;
    const rightWidth = clamp(viewing ? reader ?? width*.49 : inspector ?? clamp(width*.23,292,370),280,rightMax);
    return {leftWidth,leftMax,rightWidth,rightMax,setRight:viewing?setReader:setInspector,
      style:{"--nav-size":leftWidth+"px","--inspector-size":rightWidth+"px"} as CSSProperties};
  }
  return {columns,setLeft,compact,setCompact};
}

export function Splitter({side,value,min,max,change}: {side:"left"|"right";value:number;min:number;max:number;change:(v:number)=>void}) {
  const drag = useRef<{id:number;point:number;value:number} | null>(null);
  const direction = side==="left"?1:-1;
  const title="Resize "+side+" sidebar";
  return <div className={"splitter "+side+"-splitter"} role="separator" aria-label={title} title={title}
    aria-orientation="vertical" aria-controls={side==="left"?"project-nav":"research-panels"}
    aria-valuemin={min} aria-valuemax={Math.max(min,Math.round(max))} aria-valuenow={Math.round(value)} tabIndex={0}
    onPointerDown={e=>{
      if(e.button!==0 || !e.isPrimary)return;
      drag.current={id:e.pointerId,point:e.clientX,value};
      e.currentTarget.setPointerCapture(e.pointerId);
    }} onPointerMove={e=>{
      const start=drag.current;
      if(start?.id===e.pointerId) change(clamp(start.value+(e.clientX-start.point)*direction,min,max));
    }} onPointerUp={()=>{drag.current=null;}} onLostPointerCapture={()=>{drag.current=null;}}
    onKeyDown={e=>{
      const step=e.shiftKey?64:16;
      const next=e.key==="ArrowRight"?value+step*direction:e.key==="ArrowLeft"?value-step*direction:e.key==="Home"?min:e.key==="End"?max:null;
      if(next!==null){e.preventDefault();change(clamp(next,min,max));}
    }}/>;
}
