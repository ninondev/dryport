// SPDX-License-Identifier: GPL-3.0-only
// Copyright (c) 2026 Ninon
/* Headless D3D11 device creation under Wine: which implementation answers, DXMT (winemetal) or Wine's own d3d11?
 * Prints feature level, adapter description and whether winemetal.dll is loaded; needs no window. Built for both
 * PE32 and PE32+ by tools/d3d11-probe.py so the 32-bit DXMT install (2026-09-13) can be verified without a game. */
#define COBJMACROS
#define INITGUID
#include <windows.h>
#include <initguid.h>
#include <d3d11.h>
#include <dxgi.h>
#include <stdio.h>

int main(void) {
    ID3D11Device *dev = NULL; ID3D11DeviceContext *ctx = NULL; D3D_FEATURE_LEVEL fl = 0;
    static const D3D_FEATURE_LEVEL want[] = { D3D_FEATURE_LEVEL_11_1, D3D_FEATURE_LEVEL_11_0, D3D_FEATURE_LEVEL_10_1, D3D_FEATURE_LEVEL_10_0 };
    HRESULT hr = D3D11CreateDevice(NULL, D3D_DRIVER_TYPE_HARDWARE, NULL, 0, want, 4, D3D11_SDK_VERSION, &dev, &fl, &ctx);
    printf("arch=%u D3D11CreateDevice hr=0x%08lx feature_level=0x%x\n", (unsigned)(sizeof(void *) * 8), (unsigned long)hr, (unsigned)fl);
    printf("winemetal.dll loaded=%d\n", GetModuleHandleA("winemetal.dll") != NULL);
    if (dev) {
        IDXGIDevice *dx = NULL;
        if (SUCCEEDED(ID3D11Device_QueryInterface(dev, &IID_IDXGIDevice, (void **)&dx)) && dx) {
            IDXGIAdapter *ad = NULL;
            if (SUCCEEDED(IDXGIDevice_GetAdapter(dx, &ad)) && ad) {
                DXGI_ADAPTER_DESC d;
                if (SUCCEEDED(IDXGIAdapter_GetDesc(ad, &d)))
                    printf("adapter=%ls vendor=0x%04x vram_mb=%llu\n", d.Description, (unsigned)d.VendorId, (unsigned long long)(d.DedicatedVideoMemory >> 20));
                IDXGIAdapter_Release(ad);
            }
            IDXGIDevice_Release(dx);
        }
        if (ctx) ID3D11DeviceContext_Release(ctx);
        ID3D11Device_Release(dev);
    }
    fflush(stdout);
    return dev ? 0 : 1;
}
