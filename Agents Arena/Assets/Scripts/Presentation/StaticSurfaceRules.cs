using System;

namespace Actor.Presentation
{
    // Pure public-geometry gate. The supplied query sees opaque authored geometry,
    // never an actor roster, role, event or server authority.
    public static class StaticSurfaceRules
    {
        public static bool Segment(float x, float y, float radius, float ax, float ay, float bx, float by,
            float nx, float ny, Func<float, float, bool> clear)
        {
            if (!float.IsFinite(x)||!float.IsFinite(y)||!float.IsFinite(radius)||!float.IsFinite(ax)||!float.IsFinite(ay)
                ||!float.IsFinite(bx)||!float.IsFinite(by)||!float.IsFinite(nx)||!float.IsFinite(ny)||radius<=0||clear==null) return false;
            float dx=bx-ax, dy=by-ay, length=dx*dx+dy*dy;
            if (length < .0001f || nx*nx+ny*ny < .5f || (x-(ax+bx)*.5f)*nx+(y-(ay+by)*.5f)*ny <= 0) return false;
            float t=Math.Clamp(((x-ax)*dx+(y-ay)*dy)/length,0,1);
            if (Squared(x-ax-dx*t,y-ay-dy*t)>radius*radius) return false;
            for(int i=0;i<5;++i) if(!clear(ax+dx*i/4,ay+dy*i/4)) return false;
            return true;
        }
        public static bool Bounds(float x,float y,float radius,float minX,float minY,float maxX,float maxY,Func<float,float,bool> clear)
        {
            if(!float.IsFinite(x)||!float.IsFinite(y)||!float.IsFinite(radius)||!float.IsFinite(minX)||!float.IsFinite(minY)
                ||!float.IsFinite(maxX)||!float.IsFinite(maxY)||radius<=0||minX>=maxX||minY>=maxY||clear==null)return false;
            if(Squared(x-Math.Clamp(x,minX,maxX),y-Math.Clamp(y,minY,maxY))>radius*radius)return false;
            return clear(minX,minY)&&clear(maxX,minY)&&clear(minX,maxY)&&clear(maxX,maxY)&&clear((minX+maxX)*.5f,(minY+maxY)*.5f);
        }
        private static float Squared(float x,float y)=>x*x+y*y;
    }
}
