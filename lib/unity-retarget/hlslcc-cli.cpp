// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
// hlslcc-cli: DXBC -> GLSL/MSL via Unity's HLSLcc, with reflection as JSON on stdout.
// usage: hlslcc-cli <dxbc-file> <lang> <flags-hex> <out-src-file> [extAttribLoc=1]
//   lang: 150 | 410 | metal | es300
#include "hlslcc.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include <fstream>
#include <sstream>
static std::string jesc(const std::string&s){std::string o;for(char c:s){if(c=='"'||c=='\\'){o+='\\';o+=c;}else if(c=='\n')o+="\\n";else o+=c;}return o;}
struct Refl: public HLSLccReflection{
  std::vector<std::string> lines;
  void OnDiagnostics(const std::string&e,int line,bool isError) override { fprintf(stderr,"%s(line %d): %s\n", isError?"error":"diag", line, e.c_str()); }
  void OnInputBinding(const std::string&n,int b) override { lines.push_back("{\"k\":\"input\",\"name\":\""+jesc(n)+"\",\"bind\":"+std::to_string(b)+"}"); }
  bool OnConstantBuffer(const std::string&n,size_t sz,size_t mc) override { lines.push_back("{\"k\":\"cb\",\"name\":\""+jesc(n)+"\",\"size\":"+std::to_string(sz)+",\"members\":"+std::to_string(mc)+"}"); return true; }
  bool OnConstant(const std::string&n,int bi,SHADER_VARIABLE_TYPE t,int rows,int cols,bool isM,int asz,bool used) override {
    lines.push_back("{\"k\":\"const\",\"name\":\""+jesc(n)+"\",\"offset\":"+std::to_string(bi)+",\"type\":"+std::to_string((int)t)+",\"rows\":"+std::to_string(rows)+",\"cols\":"+std::to_string(cols)+",\"isMatrix\":"+(isM?"true":"false")+",\"arraySize\":"+std::to_string(asz)+",\"used\":"+(used?"true":"false")+"}"); return true; }
  void OnConstantBufferBinding(const std::string&n,int b) override { lines.push_back("{\"k\":\"cbbind\",\"name\":\""+jesc(n)+"\",\"bind\":"+std::to_string(b)+"}"); }
  void OnTextureBinding(const std::string&n,int b,int s,bool ms,HLSLCC_TEX_DIMENSION d,bool uav) override { lines.push_back("{\"k\":\"tex\",\"name\":\""+jesc(n)+"\",\"bind\":"+std::to_string(b)+",\"sampler\":"+std::to_string(s)+",\"ms\":"+(ms?"true":"false")+",\"dim\":"+std::to_string((int)d)+",\"uav\":"+(uav?"true":"false")+"}"); }
  void OnBufferBinding(const std::string&n,int b,bool uav) override { lines.push_back("{\"k\":\"buf\",\"name\":\""+jesc(n)+"\",\"bind\":"+std::to_string(b)+",\"uav\":"+(uav?"true":"false")+"}"); }
  void OnVertexProgramOutput(const std::string&n,const std::string&sem,int idx) override { lines.push_back("{\"k\":\"vsout\",\"name\":\""+jesc(n)+"\",\"sem\":\""+jesc(sem)+"\",\"idx\":"+std::to_string(idx)+"}"); }
  void OnFragmentOutputDeclaration(int nc,int oi) override { lines.push_back("{\"k\":\"fsout\",\"comps\":"+std::to_string(nc)+",\"index\":"+std::to_string(oi)+"}"); }
};
int main(int argc,char**argv){
  if(argc<5){fprintf(stderr,"usage: %s dxbc lang flagshex out [extAttribLoc]\n",argv[0]);return 2;}
  std::ifstream f(argv[1],std::ios::binary); std::string data((std::istreambuf_iterator<char>(f)),std::istreambuf_iterator<char>());
  if(data.size()<4){fprintf(stderr,"read fail\n");return 2;}
  GLLang lang=LANG_150; std::string l=argv[2];
  if(l=="150")lang=LANG_150; else if(l=="410")lang=LANG_410; else if(l=="330")lang=LANG_330; else if(l=="metal")lang=LANG_METAL; else if(l=="es300")lang=LANG_ES_300; else if(l=="es310")lang=LANG_ES_310; else {fprintf(stderr,"bad lang\n");return 2;}
  unsigned flags=(unsigned)strtoul(argv[3],0,16);
  GlExtensions ext; memset(&ext,0,sizeof(ext)); ext.ARB_explicit_attrib_location = (argc>5)?atoi(argv[5]):1; ext.ARB_explicit_uniform_location=0; ext.ARB_shading_language_420pack=0;
  GLSLCrossDependencyData deps; HLSLccSamplerPrecisionInfo prec; Refl refl; GLSLShader result;
  int ok=TranslateHLSLFromMem(data.data(),flags,lang,&ext,&deps,prec,refl,&result);
  if(!ok){fprintf(stderr,"translate failed\n");return 1;}
  std::ofstream o(argv[4],std::ios::binary); o<<result.sourceCode; o.close();
  printf("{\"shaderType\":%d,\"lines\":[",result.shaderType);
  for(size_t i=0;i<refl.lines.size();i++){printf("%s%s",i?",":"",refl.lines[i].c_str());}
  printf("]}\n");
  return 0;
}
