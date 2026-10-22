using System;

namespace Actor.Presentation
{
    // Panel-space decoration only. Inputs must come from currently visible spawned avatars.
    public static class NameplateLayoutRules
    {
        public const int Capacity = 8;
        public const float Width = 88, Height = 22;
        private const float Gap = 2;
        private static readonly float[] Offsets = { 0, -24, 24, -48, 48, -72, 72, -96, 96, -120, 120, -144, 144, -168, 168 };

        public struct Entry
        {
            public ulong Id;
            public int SourceIndex;
            public float X, Y;
            public bool Placed;
        }

        // Sort/place the supplied bounded reusable buffer in place. No per-frame allocation.
        public static void Arrange(Entry[] entries, int count, float width, float height)
        {
            if (entries == null || count < 0 || count > entries.Length || count > Capacity)
                throw new ArgumentOutOfRangeException(nameof(count));
            for (int i = 0; i < count; i++) entries[i].Placed = false;
            if (!float.IsFinite(width) || !float.IsFinite(height) || width < Width || height < Height) return;
            for (int i = 1; i < count; i++)
            {
                Entry value = entries[i]; int j = i - 1;
                while (j >= 0 && entries[j].Id > value.Id)
                { entries[j + 1] = entries[j]; j--; }
                entries[j + 1] = value;
            }
            for (int i = 0; i < count; i++)
            {
                Entry value = entries[i];
                if (!float.IsFinite(value.X) || !float.IsFinite(value.Y)) continue;
                float x = Math.Clamp(value.X, 0, width - Width);
                float originY = Math.Clamp(value.Y, 0, height - Height);
                foreach (float offset in Offsets)
                {
                    float y = originY + offset;
                    if (y < 0 || y + Height > height) continue;
                    bool overlaps = false;
                    for (int j = 0; j < i; j++)
                    {
                        Entry other = entries[j];
                        if (other.Placed && x < other.X + Width + Gap && x + Width + Gap > other.X
                            && y < other.Y + Height + Gap && y + Height + Gap > other.Y)
                        { overlaps = true; break; }
                    }
                    if (overlaps) continue;
                    value.X = x; value.Y = y; value.Placed = true; break;
                }
                entries[i] = value;
            }
        }
    }
}
