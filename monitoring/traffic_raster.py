"""Preserve continuous provider ribbons separately from graph assignments."""
def ribbon_overlay(before, after, region):
    import cv2
    import numpy as np
    from PIL import Image
    from monitoring.traffic_image import color_mask
    x,y,w,h=map(int,region);labels=color_mask(before,after)[y:y+h,x:x+w]
    # Reconstruct changed white arrow glyphs only. Unchanged road gaps stay empty.
    old=np.asarray(before.convert('RGB'),dtype=np.int16)[y:y+h,x:x+w]
    new=np.asarray(after.convert('RGB'),dtype=np.int16)[y:y+h,x:x+w]
    arrow=(new.min(axis=2)>=235)&(np.max(np.abs(new-old),axis=2)>=8)&(labels==0)
    repaired={}
    for label in (1,2,3):
        mask=(labels==label).astype(np.uint8);closed=np.zeros_like(mask)
        for angle in range(0,180,15):
            kernel=np.zeros((11,11),dtype=np.uint8)
            theta=np.deg2rad(angle);dx=int(round(5*np.cos(theta)));dy=int(round(5*np.sin(theta)))
            cv2.line(kernel,(5-dx,5-dy),(5+dx,5+dy),1,1)
            closed|=cv2.morphologyEx(mask,cv2.MORPH_CLOSE,kernel)
        repaired[label]=arrow&(closed>0)
    votes=sum(v.astype(np.uint8) for v in repaired.values())
    for label,repair in repaired.items():labels[repair&(votes==1)]=label
    result=np.zeros((h,w,4),dtype=np.uint8)
    colors={1:(25,132,59,255),2:(222,153,0,255),3:(210,43,41,255)}
    for label,color in colors.items():
        mask=(labels==label).astype(np.uint8)
        count,components,stats,_=cv2.connectedComponentsWithStats(mask,connectivity=8)
        allowed=np.zeros(count,dtype=bool)
        for i in range(1,count):
            width,height,area=stats[i,cv2.CC_STAT_WIDTH],stats[i,cv2.CC_STAT_HEIGHT],stats[i,cv2.CC_STAT_AREA]
            extent=max(width,height);aspect=extent/max(1,min(width,height));fill=area/(width*height)
            allowed[i]=extent>=10 and not(extent<40 and aspect<1.8 and fill>.3)
        result[allowed[components]]=color
    return Image.fromarray(result)
