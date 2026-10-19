/* private-gate.js: owner-only gate for Jackson's personal apps (Crew Map, YKF Arrivals). Needs jwauth.js.
   PrivateGate.open({title,icon,ownerUid,probe}) resolves with the user once they are confirmed as the owner:
   - ownerUid matches, or
   - the probe doc (e.g. "crewMap/roster") can be read. Firestore rules only let Jackson's uid read it,
     so everyone else gets "permission denied" and sees the private screen instead. Nothing is ever shown to them. */
(function(){
'use strict';
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function privateScreen(o,u){JWAUTH.css&&JWAUTH.css();let el=document.getElementById('jwg');if(!el){el=document.createElement('div');el.id='jwg';el.className='jwg';document.body.appendChild(el)}
 el.innerHTML=`<div class="in">${o.icon?`<img class="ic" src="${esc(o.icon)}" alt="">`:''}<h1>Private app</h1><p>${esc(o.title)} is a personal app for Jackson’s crew, so there’s nothing to show for this account.</p>
 <p class="sm">Signed in as ${esc(u.email||u.displayName||'this account')}.</p><button class="gh" data-a="out">Sign out</button></div>`;
 el.querySelector('[data-a="out"]').onclick=async()=>{await JWAUTH.signOut();location.reload()}}
async function check(o,u){if(o.ownerUid&&u.uid===o.ownerUid)return true;try{await JWAUTH.getDoc(o.probe);return true}catch(e){if(!JWAUTH.denied(e))console.warn('gate probe',e);return false}}
function open(o){return new Promise(async res=>{
 const go=async u=>{const ok=await check(o,u);if(ok){const el=document.getElementById('jwg');el&&el.remove();res(u)}else privateScreen(o,u)};
 try{const u=await JWAUTH.ready();if(u)return go(u)}catch(e){console.warn(e)}
 JWAUTH.gate({title:o.title,icon:o.icon,sub:'This is a private app. Sign in with the owner’s account to open it.',onUser:go})})}
window.PrivateGate={open};
})();
