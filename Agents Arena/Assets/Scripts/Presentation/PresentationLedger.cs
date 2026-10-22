using System;
using System.Collections.Generic;

namespace Actor.Presentation
{
    // Pure bounded replay/freshness gate. NetworkSocialMatch performs authority
    // and recipient filtering before this cosmetic gate ever sees an event.
    public sealed class PresentationLedger
    {
        private readonly HashSet<string> seen=new(StringComparer.Ordinal);
        private readonly Queue<string> order=new();
        private string match="";
        public int Count=>seen.Count;
        public bool Accept(string expectedMatch,string self,string eventMatch,string recipient,string id,string bodyId,string victim,float x,float y,long at,long now)
        {
            if(string.IsNullOrEmpty(expectedMatch)||expectedMatch!=eventMatch||string.IsNullOrEmpty(self)||self!=recipient
                ||!Guid.TryParseExact(id,"N",out _)||!Guid.TryParseExact(bodyId,"N",out _)||string.IsNullOrEmpty(victim)||victim.Length>128
                ||!float.IsFinite(x)||!float.IsFinite(y)||at<=0||at>now+1000||at<now-3000)return false;
            if(match!=expectedMatch){seen.Clear();order.Clear();match=expectedMatch;}
            if(!seen.Add(id))return false;order.Enqueue(id);
            while(order.Count>256)seen.Remove(order.Dequeue());return true;
        }
    }
}
