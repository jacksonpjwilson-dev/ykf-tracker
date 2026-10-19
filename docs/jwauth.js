/* jwauth.js: shared sign-in for Jackson's apps on jacksonpjwilson-dev.github.io (Firebase project logbook-e784a).
   Same project + same origin as Logbook v2 and Pantry, so one sign-in covers them all (and signing out signs out everywhere).
   Exposes window.JWAUTH: ready(), user(), db(), google(), email(), reset(), signOut(), gate({...}), getDoc(path), setDoc(path,data,merge), denied(err).
   Copied verbatim into each app repo; keep the copies identical. */
(function(){
'use strict';
const CFG={apiKey:"AIzaSyAe50ZOlninoeHPIrxq9aylnLROi3SFekY",authDomain:"logbook-e784a.firebaseapp.com",projectId:"logbook-e784a",storageBucket:"logbook-e784a.firebasestorage.app",messagingSenderId:"901165633464",appId:"1:901165633464:web:f201b53121ccf05f04c94d"};
const SDK='https://www.gstatic.com/firebasejs/10.13.2/';
let fb=null,initP=null;
const load=src=>new Promise((res,rej)=>{const s=document.createElement('script');s.src=SDK+src;s.onload=res;s.onerror=()=>rej(new Error('Could not load sign-in (offline?)'));document.head.appendChild(s)});
function init(){if(initP)return initP;initP=(async()=>{if(!window.firebase){await load('firebase-app-compat.js');await load('firebase-auth-compat.js');await load('firebase-firestore-compat.js')}
  if(!(firebase.apps&&firebase.apps.length))firebase.initializeApp(CFG);fb={auth:firebase.auth(),db:firebase.firestore()};try{await fb.auth.getRedirectResult()}catch(e){}return fb})();return initP}
const ready=()=>init().then(f=>new Promise(res=>{const off=f.auth.onAuthStateChanged(u=>{off&&off();res(u)})}));
const standalone=()=>navigator.standalone||matchMedia('(display-mode: standalone)').matches;
async function google(){await init();const p=new firebase.auth.GoogleAuthProvider();if(standalone()){await fb.auth.signInWithRedirect(p);return null}
 try{return (await fb.auth.signInWithPopup(p)).user}catch(e){if(/popup-blocked|operation-not-supported/.test(e.code||'')){await fb.auth.signInWithRedirect(p);return null}throw e}}
async function email(em,pw,create){await init();return (create?await fb.auth.createUserWithEmailAndPassword(em,pw):await fb.auth.signInWithEmailAndPassword(em,pw)).user}
async function reset(em){await init();return fb.auth.sendPasswordResetEmail(em)}
async function signOut(){await init();return fb.auth.signOut()}
const denied=e=>/permission|insufficient/i.test(String(e&&(e.code||'')+' '+(e.message||e)));
const errMsg=e=>{const c=String(e&&e.code||'');return /wrong-password|invalid-credential|user-not-found/.test(c)?'Email or password is not right.':/email-already/.test(c)?'That email already has an account. Sign in instead.':/weak-password/.test(c)?'Use at least 6 characters.':/invalid-email/.test(c)?'That email doesn’t look right.':/popup-closed|cancelled/.test(c)?'Sign-in was cancelled.':/network|unavailable/.test(c)?'You look offline. Try again.':denied(e)?'Access denied by the security rules.':(e&&e.message)||String(e)};
async function getDoc(path){await init();const s=await fb.db.doc(path).get();return s.exists?s.data():null}
async function setDoc(path,data,merge){await init();return fb.db.doc(path).set(data,merge?{merge:true}:undefined)}
async function getCol(path){await init();const q=await fb.db.collection(path).get();const o={};q.forEach(d=>o[d.id]=d.data());return o}
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const GICON='<svg width="20" height="20" viewBox="0 0 48 48" aria-hidden="true"><path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z"/><path fill="#FF3D00" d="m6.3 14.7 6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/><path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z"/><path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z"/></svg>';
const CSS=`.jwg{position:fixed;inset:0;z-index:9999;overflow-y:auto;display:flex;align-items:center;justify-content:center;padding:calc(env(safe-area-inset-top) + 24px) 22px calc(env(safe-area-inset-bottom) + 24px);background:var(--jwg-bg,#0b1020);color:var(--jwg-fg,#e8ecf5);font-family:-apple-system,BlinkMacSystemFont,'Inter','Segoe UI',sans-serif}
.jwg .in{width:100%;max-width:400px;text-align:center}.jwg .ic{width:84px;height:84px;border-radius:22px;margin:0 auto 14px;display:block;box-shadow:0 10px 30px rgba(0,0,0,.35)}
.jwg h1{font-size:28px;margin:4px 0 6px;letter-spacing:-.02em}.jwg p{color:var(--jwg-mut,#9aa4b8);margin:0 0 18px;font-size:15px;line-height:1.45}
.jwg button,.jwg input{font:inherit;width:100%;min-height:50px;border-radius:14px;margin-top:10px;font-size:16px}
.jwg input{border:1px solid var(--jwg-line,#2a3350);background:var(--jwg-card,#141b30);color:inherit;padding:0 14px;text-align:left}
.jwg button{border:0;font-weight:650;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:10px}
.jwg .g{background:#fff;color:#1f2937}.jwg .p{background:var(--jwg-accent,#3b82f6);color:#fff}.jwg .gh{background:transparent;color:var(--jwg-fg,#e8ecf5);border:1px solid var(--jwg-line,#2a3350)}
.jwg .lk{background:none;min-height:36px;color:var(--jwg-mut,#9aa4b8);font-weight:500;font-size:14px}.jwg .er{color:#f87171;font-size:14px;min-height:18px;margin:10px 0 0}.jwg .sm{font-size:12.5px;margin-top:14px}
.jwg .seg{display:flex;gap:6px;background:var(--jwg-card,#141b30);border-radius:14px;padding:4px;margin-top:4px}.jwg .seg button{margin:0;min-height:40px;background:transparent;color:var(--jwg-mut,#9aa4b8)}.jwg .seg button.on{background:var(--jwg-line,#2a3350);color:var(--jwg-fg,#e8ecf5)}`;
function css(){if(document.getElementById('jwg-css'))return;const s=document.createElement('style');s.id='jwg-css';s.textContent=CSS;document.head.appendChild(s)}
/* Full-screen sign-in. opts: {title, sub, icon, note, email:true, onUser(user), extra:[{label,onClick}]} */
function gate(o){css();let el=document.getElementById('jwg');if(!el){el=document.createElement('div');el.id='jwg';el.className='jwg';document.body.appendChild(el)}
 let mode='start',create=false,err='';
 const draw=()=>{el.innerHTML=`<div class="in">${o.icon?`<img class="ic" src="${esc(o.icon)}" alt="">`:''}<h1>${esc(o.title||'Sign in')}</h1><p>${o.sub||''}</p>
  ${mode==='start'?`<button class="g" data-a="g">${GICON} Continue with Google</button>${o.email!==false?'<button class="gh" data-a="e">Sign in with email</button>':''}${(o.extra||[]).map((x,i)=>`<button class="lk" data-x="${i}">${esc(x.label)}</button>`).join('')}`
  :`<div class="seg"><button data-m="in" class="${create?'':'on'}">Sign in</button><button data-m="up" class="${create?'on':''}">Create account</button></div><input id="jwem" type="email" autocomplete="email" placeholder="Email"><input id="jwpw" type="password" autocomplete="${create?'new-password':'current-password'}" placeholder="Password"><button class="p" data-a="go">${create?'Create account':'Sign in'}</button><button class="lk" data-a="fp">Forgot password?</button><button class="lk" data-a="bk">‹ Back</button>`}
  <div class="er">${esc(err)}</div>${o.note?`<p class="sm">${o.note}</p>`:''}</div>`;
  el.querySelectorAll('[data-a],[data-m],[data-x]').forEach(b=>b.onclick=async()=>{const a=b.dataset.a;err='';
   if(b.dataset.x!=null){return o.extra[+b.dataset.x].onClick()}
   if(b.dataset.m){create=b.dataset.m==='up';const em=el.querySelector('#jwem').value;draw();el.querySelector('#jwem').value=em;return}
   if(a==='e'){mode='email';return draw()}if(a==='bk'){mode='start';return draw()}
   try{let u=null;if(a==='g')u=await google();
    else if(a==='go'){const em=el.querySelector('#jwem').value.trim(),pw=el.querySelector('#jwpw').value;if(!em||!pw){err='Enter your email and password.';return draw()}u=await email(em,pw,create)}
    else if(a==='fp'){const em=el.querySelector('#jwem').value.trim();if(!em){err='Type your email first.';return draw()}await reset(em);err='If that email has an account, a reset link is on its way.';return draw()}
    if(u){el.remove();o.onUser&&o.onUser(u)}}catch(e){err=errMsg(e);draw()}})};
 draw();return {close:()=>el.remove()}}
window.JWAUTH={css,init,ready,google,email,reset,signOut,gate,getDoc,setDoc,getCol,denied,errMsg,user:()=>fb&&fb.auth.currentUser,db:()=>fb&&fb.db,CFG};
})();
