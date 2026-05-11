"use client";

import Script from "next/script";
import { useEffect, useMemo, useState } from "react";
import { MapPin } from "lucide-react";
import type { Transaction } from "../../lib/platform-data";

interface KakaoLatLngOptions {
  center: unknown;
  level: number;
}

interface KakaoOverlayOptions {
  position: unknown;
  content: string;
  map: unknown;
}

interface KakaoMaps {
  load(callback: () => void): void;
  LatLng: new (lat: number, lng: number) => unknown;
  Map: new (container: HTMLElement, options: KakaoLatLngOptions) => unknown;
  CustomOverlay: new (options: KakaoOverlayOptions) => unknown;
}

declare global {
  interface Window {
    kakao?: {
      maps: KakaoMaps;
    };
  }
}

interface KakaoMapProps {
  transactions?: Transaction[];
}

const DEFAULT_CENTER = {
  lat: 37.4979,
  lng: 127.0276,
};

export function KakaoMap({ transactions = [] }: KakaoMapProps) {
  const [isLoaded, setIsLoaded] = useState(false);
  const appKey = process.env.NEXT_PUBLIC_KAKAO_APP_KEY;

  const center = useMemo(() => {
    const first = transactions[0];
    return first ? { lat: first.lat, lng: first.lng } : DEFAULT_CENTER;
  }, [transactions]);

  useEffect(() => {
    if (!appKey || !isLoaded || !window.kakao?.maps) {
      return;
    }

    const kakaoMaps = window.kakao.maps;

    kakaoMaps.load(() => {
      const container = document.getElementById("kakao-map-container");
      if (!container) {
        return;
      }

      const targetPosition = new kakaoMaps.LatLng(center.lat, center.lng);
      const map = new kakaoMaps.Map(container, {
        center: targetPosition,
        level: 4,
      });

      new kakaoMaps.CustomOverlay({
        position: targetPosition,
        content: markerHtml("분석 대상지", "bg-slate-950", true),
        map,
      });

      transactions.forEach((transaction) => {
        new kakaoMaps.CustomOverlay({
          position: new kakaoMaps.LatLng(transaction.lat, transaction.lng),
          content: markerHtml(transaction.unit_price_krw, "bg-indigo-600", false),
          map,
        });
      });
    });
  }, [appKey, center.lat, center.lng, isLoaded, transactions]);

  if (!appKey) {
    return (
      <div className="relative flex min-h-[480px] flex-1 overflow-hidden bg-slate-100 dark:bg-slate-900">
        <div className="absolute inset-0 opacity-70">
          <div className="grid h-full grid-cols-6 grid-rows-6">
            {Array.from({ length: 36 }, (_, index) => (
              <div
                key={index}
                className="border border-white/70 bg-slate-100 dark:border-slate-800 dark:bg-slate-900"
              />
            ))}
          </div>
        </div>
        <div className="absolute left-[52%] top-[48%] -translate-x-1/2 -translate-y-1/2">
          <div className="flex h-14 w-14 items-center justify-center rounded-full bg-slate-950 text-white shadow-lg dark:bg-white dark:text-slate-950">
            <MapPin className="h-7 w-7" />
          </div>
        </div>
        <div className="relative z-10 m-auto w-full max-w-md rounded-lg border border-slate-200 bg-white/95 p-5 text-center shadow-sm backdrop-blur dark:border-slate-800 dark:bg-slate-950/95">
          <h3 className="text-lg font-bold text-slate-950 dark:text-white">
            지도 API 키가 필요합니다
          </h3>
          <p className="mt-2 text-sm leading-6 text-slate-600 dark:text-slate-400">
            `NEXT_PUBLIC_KAKAO_APP_KEY`가 없을 때는 샘플 격자 지도로 대상지와 거래
            데이터를 표시합니다.
          </p>
          <div className="mt-4 rounded-lg bg-slate-100 p-3 text-left font-mono text-xs text-slate-600 dark:bg-slate-900 dark:text-slate-300">
            NEXT_PUBLIC_KAKAO_APP_KEY=your_key_here
          </div>
        </div>
      </div>
    );
  }

  return (
    <>
      <Script
        src={`//dapi.kakao.com/v2/maps/sdk.js?appkey=${appKey}&autoload=false`}
        strategy="afterInteractive"
        onLoad={() => setIsLoaded(true)}
      />
      <div id="kakao-map-container" className="min-h-[480px] flex-1" />
    </>
  );
}

function markerHtml(label: string, colorClass: string, hasTail: boolean) {
  const bgColor = colorClass === "bg-indigo-600" ? "#4f46e5" : "#020617";
  const tail = hasTail
    ? `<div style="position:absolute;bottom:-7px;left:50%;transform:translateX(-50%);border-left:7px solid transparent;border-right:7px solid transparent;border-top:7px solid ${bgColor};"></div>`
    : "";

  return `
    <div style="position:relative;display:inline-flex;align-items:center;justify-content:center;min-width:72px;padding:6px 10px;border-radius:8px;background:${bgColor};color:#fff;font-size:12px;font-weight:800;box-shadow:0 12px 24px rgba(15,23,42,.22);white-space:nowrap;">
      ${label}
      ${tail}
    </div>
  `;
}
