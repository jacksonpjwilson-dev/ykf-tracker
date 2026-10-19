/* YKF Arrivals: one switch for the move to private data.
   PRIVATE:false = today: live.json from the public data branch, no sign-in.
   PRIVATE:true  = owner-only: Google sign-in (shared with Logbook/Pantry), data from Firestore ykf/live; everyone else sees "Private app".
   Flip only after the Firestore rules are published and the box writer is running. Preview the gate with ?private=1. */
window.YKF_CFG={PRIVATE:false,OWNER_UID:''};
