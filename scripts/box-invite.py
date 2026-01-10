#!/usr/bin/env python3
"""
Usage:
  Create invite: ./box-invite.py create --secret SECRET --profile dev --ttl 600
  Client helper: ./box-invite.py connect --token <TOKEN> --gateway <http://localhost:8080> --privkey-path ./id_box
"""
import argparse, hmac, hashlib, time, json, os, subprocess, tempfile, requests

def create_invite(secret, profile='dev', ttl=600):
    # token payload: profile:ttl:timestamp:signature
    ts=str(int(time.time()))
    payload=f"{profile}:{ttl}:{ts}"
    sig=hmac.new(secret.encode(),'{}'.format(payload).encode(),hashlib.sha256).hexdigest()
    token=f"{payload}:{sig}"
    print(token)
    return token

def client_connect(token, gateway, privkey_path=None):
    # generate keypair locally
    if privkey_path is None:
        privkey_path = "./id_box"
    pubkey_path = privkey_path + ".pub"
    subprocess.run(["ssh-keygen","-t","ed25519","-f",privkey_path,"-N",""], check=True)
    with open(pubkey_path,'r') as f: pubkey=f.read().strip()
    # POST to gateway
    resp = requests.post(gateway+"/request", json={"token": token, "pubkey": pubkey, "profile":"dev", "ttl":300})
    if resp.status_code!=200:
        print("Gateway error:", resp.text); return
    info=resp.json()
    host=info['host']; port=info['port']; user=info['user']
    # connect via ssh
    ssh_cmd=["ssh","-i",privkey_path,f"{user}@{host}","-p",str(port)]
    print("Connecting:", " ".join(ssh_cmd))
    os.execvp("ssh", ssh_cmd)

if __name__ == "__main__":
    p=argparse.ArgumentParser()
    sub=p.add_subparsers(dest='cmd')
    c=sub.add_parser('create'); c.add_argument('--secret',required=True); c.add_argument('--profile',default='dev'); c.add_argument('--ttl',type=int,default=600)
    k=sub.add_parser('connect'); k.add_argument('--token',required=True); k.add_argument('--gateway',default='http://localhost:8080'); k.add_argument('--privkey-path',default='./id_box')
    args=p.parse_args()
    if args.cmd=='create':
        create_invite(args.secret, args.profile, args.ttl)
    elif args.cmd=='connect':
        client_connect(args.token, args.gateway, args.privkey_path)