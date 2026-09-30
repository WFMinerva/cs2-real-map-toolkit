// HarbinBridgeLift —— 地形高度采样
//
// 为什么要采样地形而不是直接用 Node.m_Position.y：
//   已经抬过一次的节点，它的 y 已经不是地形高度了。用「地形高度 + 净空」算目标值
//   才能保证反复点也幂等。
//
// 用反射而不是直接引用 Game.Simulation.TerrainSystem / TerrainUtils：
//   跟 Earth2Cities 的 TerrainHeightSampler 一样，规避这两个类型的可见性/版本问题。

using System;
using System.Linq;
using System.Reflection;
using Unity.Entities;
using Unity.Mathematics;

namespace HarbinBridgeLift
{
    internal static class TerrainSampler
    {
        private static bool _init;
        private static bool _ok;
        private static Type _systemType;
        private static Type _utilsType;
        private static MethodInfo _getOrCreate;
        private static MethodInfo _getHeightData;
        private static MethodInfo _sampleHeight;

        public static string Diag { get; private set; } = "未初始化";

        private static void EnsureInit()
        {
            if (_init) return;
            _init = true;
            try
            {
                Assembly[] asms = AppDomain.CurrentDomain.GetAssemblies();
                _systemType = asms.Select(a => a.GetType("Game.Simulation.TerrainSystem", false, false))
                                 .FirstOrDefault(t => t != null);
                _utilsType = asms.Select(a => a.GetType("Game.Simulation.TerrainUtils", false, false))
                                 .FirstOrDefault(t => t != null);
                if (_systemType == null || _utilsType == null)
                {
                    Diag = $"类型缺失 TerrainSystem={_systemType != null} TerrainUtils={_utilsType != null}";
                    return;
                }
                _getOrCreate = typeof(World).GetMethod("GetOrCreateSystemManaged",
                    BindingFlags.Instance | BindingFlags.Public, null, new[] { typeof(Type) }, null);
                _getHeightData = _systemType
                    .GetMethods(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
                    .FirstOrDefault(m =>
                    {
                        if (m.Name != "GetHeightData") return false;
                        ParameterInfo[] ps = m.GetParameters();
                        return ps.Length == 1 && ps[0].ParameterType == typeof(bool);
                    });
                _sampleHeight = _utilsType
                    .GetMethods(BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic)
                    .FirstOrDefault(m =>
                    {
                        if (m.Name != "SampleHeight") return false;
                        ParameterInfo[] ps = m.GetParameters();
                        return ps.Length == 2 && ps[1].ParameterType == typeof(float3);
                    });
                _ok = _getOrCreate != null && _getHeightData != null && _sampleHeight != null;
                Diag = _ok
                    ? $"OK getHeightData={_getHeightData} sampleHeight={_sampleHeight}"
                    : $"方法缺失 getOrCreate={_getOrCreate != null} getHeightData={_getHeightData != null} sampleHeight={_sampleHeight != null}";
            }
            catch (Exception ex)
            {
                _ok = false;
                Diag = "异常 " + ex.Message;
            }
        }

        /// <summary>一个采样会话；跨节点复用同一个 TerrainHeightData，避免反复反射。</summary>
        public sealed class Session
        {
            private readonly object _data;
            private readonly bool _valid;
            public int Hits;
            public int Misses;

            internal Session(object data, bool valid)
            {
                _data = data;
                _valid = valid;
            }

            public bool TrySample(float3 pos, out float height)
            {
                height = 0f;
                if (!_valid || _data == null) { Misses++; return false; }
                try
                {
                    object r = _sampleHeight.Invoke(null, new object[] { _data, pos });
                    if (r is float f && !float.IsNaN(f) && !float.IsInfinity(f))
                    {
                        height = f;
                        Hits++;
                        return true;
                    }
                }
                catch
                {
                    // 落到下面算 miss
                }
                Misses++;
                return false;
            }
        }

        public static Session Open(World world, out string why)
        {
            why = null;
            EnsureInit();
            if (!_ok) { why = Diag; return new Session(null, false); }
            try
            {
                object sys = _getOrCreate.Invoke(world, new object[] { _systemType });
                if (sys == null) { why = "TerrainSystem 为 null"; return new Session(null, false); }
                object data = _getHeightData.Invoke(sys, new object[] { false });
                if (data == null) { why = "TerrainHeightData 为 null"; return new Session(null, false); }
                return new Session(data, true);
            }
            catch (Exception ex)
            {
                why = "取地形数据异常 " + ex.Message;
                return new Session(null, false);
            }
        }
    }
}
