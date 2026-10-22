using System;
using System.Collections.Generic;
using System.Linq;
using Actor.Networking;
using AgentsArena.ActorProtocol.V1;
using Google.Protobuf;
using UnityEngine;
#if !UNITY_SERVER
using Actor.Protocol.Standard.Communication.V1;
using Actor.UI;
#endif

namespace Actor.Presentation
{
    public sealed class SocialKillPresentation : MonoBehaviour
    {
#if !UNITY_SERVER
        private NetworkSocialMatch owner;
        private NetworkCooperation communication;
        private readonly PresentationLedger ledger=new();
        private readonly HashSet<string> cueIds=new(StringComparer.Ordinal);
        private readonly Queue<string> cueOrder=new();
        private readonly List<Fold> folds=new();
        private static readonly HashSet<string> animatedBodies=new(StringComparer.Ordinal);
        private SocialPhase phase;
        private string emergency="";
        private sealed class Fold
        {
            internal string bodyId,match;
            internal bool selfVictim;
            internal GameObject root;
            internal SpriteRenderer renderer, mark;
            internal AudioSource sound;
            internal double started;
        }
        public static bool IsAnimating(string bodyId)=>!string.IsNullOrEmpty(bodyId)&&animatedBodies.Contains(bodyId);
        private bool Once(string id)
        {
            if(string.IsNullOrEmpty(id)||!cueIds.Add(id))return false;cueOrder.Enqueue(id);
            while(cueOrder.Count>256)cueIds.Remove(cueOrder.Dequeue());return true;
        }
        private void Update()
        {
            var next=NetworkSocialMatch.LocalOwner;
            if(owner!=next)
            {
                Unbind();owner=next;
                if(owner!=null){phase=owner.State.Phase;owner.PermittedEvent+=OnEvent;communication=owner.GetComponent<NetworkCooperation>();if(communication!=null)communication.PermittedEvent+=OnMessage;}
            }
            double now=Time.realtimeSinceStartupAsDouble;
            for(int i=folds.Count-1;i>=0;--i)
            {
                Fold fold=folds[i];float elapsed=(float)(now-fold.started);
                // On entering a room later, only the already-filtered static body
                // exists. The 640ms sequence is never inferred from a spawn.
                bool permitted=fold.root!=null&&owner!=null&&owner.IsSpawned&&owner.State.MatchId==fold.match
                    &&(fold.selfVictim||LocallyVisible(owner,fold.root.transform.position));
                if(!permitted||elapsed>=.64f||fold.root==null){Remove(i);continue;}
                bool reduced=ArenaUI.ReducedMotion;
                fold.renderer.sprite=AtmosphericArtCatalog.Load().folds[reduced?2:elapsed<.16f?0:elapsed<.36f?1:2];
                fold.mark.enabled=reduced||elapsed>=.36f;
                fold.root.transform.localScale=Vector3.one*(reduced?1:1+.025f*Mathf.Sin(elapsed/.64f*Mathf.PI));
                fold.renderer.color=new Color(1,1,1,Mathf.Clamp01(elapsed/.045f));
            }
        }
        private void OnMessage(IMessage value)
        {if(value is MessageReceivedEvent message&&Once("message:"+message.MessageId))ArenaAudio.PlayUI(ArenaAudio.Cue.Message);}
        private void OnEvent(IMessage value)
        {
            if(owner==null)return;
            if(value is SocialKillPresentationEvent kill)
            {
                long now=DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                if(!ledger.Accept(owner.State.MatchId,owner.Self,kill.MatchId,kill.RecipientActorId,kill.PresentationId,kill.BodyId,kill.VictimActorId,kill.X,kill.Y,kill.ServerUnixMs,now))return;
                var art=AtmosphericArtCatalog.Load();if(art==null||animatedBodies.Contains(kill.BodyId))return;
                if(kill.VictimActorId!=owner.Self&&!LocallyVisible(owner,new Vector2(kill.X,kill.Y)))return;
                if(!ResolveCosmetic(kill.VictimActorId,kill.BodyId,out int cosmetic))return;
                var root=new GameObject("Permitted mantle fold");root.transform.position=new Vector3(kill.X,kill.Y,0);
                var renderer=root.AddComponent<SpriteRenderer>();renderer.sprite=art.folds[0];renderer.sharedMaterial=art.paletteMaterial;renderer.sortingOrder=3;
                var block=new MaterialPropertyBlock();block.SetColor("_SkinColor",SocialAvatarArt.Fabric(cosmetic));renderer.SetPropertyBlock(block);
                var markRoot=new GameObject("Cosmetic Stitch");markRoot.transform.SetParent(root.transform,false);markRoot.transform.localPosition=new Vector3(.13f,-.1f,-.001f);markRoot.transform.localScale=Vector3.one*.28f;
                var mark=markRoot.AddComponent<SpriteRenderer>();mark.sprite=art.identityMarks[cosmetic];mark.sortingOrder=4;mark.enabled=false;
                var fold=new Fold{root=root,renderer=renderer,mark=mark,started=Time.realtimeSinceStartupAsDouble,bodyId=kill.BodyId,match=kill.MatchId,selfVictim=kill.VictimActorId==owner.Self};
                fold.sound=ArenaAudio.PlayWitness(fold.selfVictim?ArenaAudio.Cue.Death:ArenaAudio.Cue.KillImpact,root.transform.position);
                animatedBodies.Add(kill.BodyId);folds.Add(fold);
                // Only an already-permitted identity may drive a tiny visual-child
                // compression. Victims receive no attacker identity at all.
                if(!string.IsNullOrEmpty(kill.AttackerActorId))
                    foreach(var character in FindObjectsByType<NetworkCharacter>(FindObjectsSortMode.None))
                        if(character.IsSpawned&&character.AuthoritativeActorId==kill.AttackerActorId
                            &&(kill.AttackerActorId==owner.Self||LocallyVisible(owner,character.PresentationPosition+Vector3.up*.46f)))
                            character.GetComponent<SocialAvatarArt>()?.Impulse();
            }
            else if(value is SocialActionResultEvent action&&action.Accepted&&Once("action:"+action.CorrelationId+":"+action.Completed))
            {
                if(action.Action==SocialAction.CastVote)ArenaAudio.PlayUI(ArenaAudio.Cue.VoteSubmitted);
                else if(action.Action==SocialAction.WorkTask||action.Action==SocialAction.RepairEmergency)
                    ArenaAudio.PlayUI(action.Completed?ArenaAudio.Cue.TaskCompleted:ArenaAudio.Cue.TaskAccepted);
            }
            else if(value is SocialMatchChangedEvent changed)
            {
                if(changed.State.Phase!=phase&&changed.State.Phase==SocialPhase.Discussion)ArenaAudio.PlayUI(ArenaAudio.Cue.Meeting);
                phase=changed.State.Phase;
            }
            else if(value is SocialEmergencyChangedEvent changedEmergency)
            {if(changedEmergency.State.Active&&emergency!=changedEmergency.State.EmergencyId){emergency=changedEmergency.State.EmergencyId;ArenaAudio.PlayUI(ArenaAudio.Cue.Emergency);}}
            else if(value is SocialMatchEndedEvent ended&&Once("ended:"+ended.Summary.MatchId))ArenaAudio.PlayUI(ArenaAudio.Cue.GameOver);
        }
        private static bool ResolveCosmetic(string actor,string bodyId,out int index)
        {
            if(SocialAvatarArt.TryCosmetic(actor,out index))return true;
            foreach(var character in FindObjectsByType<NetworkCharacter>(FindObjectsSortMode.None))
                if(character.IsSpawned&&character.AuthoritativeActorId==actor){index=character.CosmeticIndex;return true;}
            foreach(var body in FindObjectsByType<SocialBody>(FindObjectsSortMode.None))
                if(body.IsSpawned&&body.BodyId==bodyId){index=body.CosmeticIndex;return true;}
            index=0;return false;
        }
        private static bool LocallyVisible(NetworkSocialMatch source,Vector2 point)
        {
            if(source==null||!source.IsSpawned||source.Own.Life!=SocialLife.Alive)return false;
            var character=source.GetComponent<NetworkCharacter>();var capsule=source.GetComponent<CapsuleCollider2D>();
            Vector2 center=(Vector2)character.PresentationPosition+(capsule!=null?capsule.offset:Vector2.zero);
            float radius=source.State.VisibilityRadius;
            return float.IsFinite(radius)&&radius>0&&Vector2.Distance(center,point)<=radius
                &&SocialVisibilitySystem.LineOfSight(center,point);
        }
        private void Remove(int index)
        {var fold=folds[index];animatedBodies.Remove(fold.bodyId);if(fold.sound!=null){fold.sound.Stop();Destroy(fold.sound.gameObject);}if(fold.root!=null)Destroy(fold.root);folds.RemoveAt(index);}
        private void Unbind()
        {if(owner!=null)owner.PermittedEvent-=OnEvent;if(communication!=null)communication.PermittedEvent-=OnMessage;communication=null;owner=null;for(int i=folds.Count-1;i>=0;--i)Remove(i);}
        private void OnDisable()=>Unbind();
        private void OnDestroy()=>Unbind();
#else
        public static bool IsAnimating(string bodyId)=>false;
#endif
    }
}
