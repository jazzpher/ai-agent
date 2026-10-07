#define _GNU_SOURCE
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <errno.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
struct ruleset { uint64_t fs; };
struct pathrule { uint64_t access; int32_t fd; } __attribute__((packed));
static void fail(const char *s) { perror(s); exit(125); }
static void rule(int fd, const char *p, uint64_t rights) {
 struct pathrule r={.access=rights,.fd=open(p,O_PATH|O_CLOEXEC)};
 if(r.fd<0) fail("open allowed runtime");
 struct stat st;
 if(fstat(r.fd,&st)) fail("runtime stat");
 if(!S_ISDIR(st.st_mode)) r.access &= (1ULL<<0)|(1ULL<<1)|(1ULL<<2)|(1ULL<<14);
 if(syscall(445,fd,1,&r,0)<0) fail("landlock rule");
 close(r.fd);
}
static void limit(int resource, rlim_t value) {
 struct rlimit r={value,value}; if(setrlimit(resource,&r)) fail("resource limit");
}
int main(int argc,char **argv) {
 // workspace, session venv, private tmp, BPF fd, runtime path count, runtimes..., command
 if(argc<8) return 125;
 int abi=syscall(444,NULL,0,1); if(abi<3) fail("Landlock ABI 3 required");
 uint64_t rights=(1ULL<<14)-1;
 if(abi>=3) rights|=1ULL<<14;
 struct ruleset r={rights}; int fd=syscall(444,&r,sizeof(r),0);
 if(fd<0) fail("landlock ruleset");
 uint64_t ro=(1ULL<<0)|(1ULL<<2)|(1ULL<<3);
 int count=atoi(argv[5]);
 for(int i=0;i<count;i++) rule(fd,argv[6+i],ro);
 rule(fd,argv[1],rights);
 // install mode signalled by a '+' prefix on venv argument
 int install=argv[2][0]=='+';const char *venv=argv[2]+install;
 rule(fd,venv,install?rights:ro);
 rule(fd,argv[3],rights);
 rule(fd,"/dev/null",(1ULL<<1)|(1ULL<<2));
 rule(fd,"/dev/urandom",1ULL<<2);
 if(prctl(PR_SET_NO_NEW_PRIVS,1,0,0,0)) fail("no_new_privs");
 if(syscall(446,fd,0)) fail("landlock restrict");
 close(fd);
 int bpf=atoi(argv[4]);
 off_t size=lseek(bpf,0,SEEK_END);lseek(bpf,0,SEEK_SET);
 if(size<=0 || size%sizeof(struct sock_filter)) fail("seccomp size");
 struct sock_filter *filters=malloc(size);
 if(read(bpf,filters,size)!=size) fail("seccomp read");
 close(bpf);
 struct sock_fprog policy={.len=size/sizeof(struct sock_filter),.filter=filters};
 if(prctl(PR_SET_SECCOMP,SECCOMP_MODE_FILTER,&policy)) fail("seccomp load");
 limit(RLIMIT_AS,536870912);limit(RLIMIT_CPU,60);limit(RLIMIT_FSIZE,16777216);
 limit(RLIMIT_NOFILE,128);limit(RLIMIT_NPROC,256);limit(RLIMIT_CORE,0);
 if(chdir(argv[1])) fail("workspace");
 execvp(argv[6+count],argv+6+count);fail("sandbox exec");
}
