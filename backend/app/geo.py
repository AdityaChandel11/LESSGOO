"""National geography reference — states, district anchors, seasonality.

Two consumers: the seeder places facilities against these anchors, and the
aggregation layer uses the centroids to position state bubbles on the map.

HONESTY NOTE for the pitch: every coordinate here is a real city or district
headquarters. `facilities` is a proportional *sample* of each state's published
PHC count (roughly 12%), not the real count — enough to make national zoom feel
real without seeding 38,000 rows. Real network size, per Rural Health
Statistics, is ~31,900 PHCs and ~6,400 CHCs; state the sample ratio out loud
rather than implying full coverage.

Seasonality differs per state on purpose. Genuinely non-IID silos are what make
the federated learning result in spec 12.2 mean anything.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StateGeo:
    code: str
    name: str
    lat: float
    lng: float
    facilities: int
    # (district name, lat, lng) — real places, so scatter stays on land.
    anchors: list[tuple[str, float, float]]
    monsoon_months: tuple[int, ...] = (6, 7, 8, 9)
    monsoon_boost: float = 0.45
    seasonal_phase: float = 0.6
    spread: float = 0.45  # degrees of jitter around an anchor
    zoom: int = 7
    tags: tuple[str, ...] = field(default=())


INDIA_STATES: list[StateGeo] = [
    StateGeo("UP", "Uttar Pradesh", 26.85, 80.95, 380, [
        ("Lucknow", 26.8467, 80.9462), ("Kanpur", 26.4499, 80.3319),
        ("Varanasi", 25.3176, 82.9739), ("Agra", 27.1767, 78.0081),
        ("Gorakhpur", 26.7606, 83.3732), ("Prayagraj", 25.4358, 81.8463),
        ("Meerut", 28.9845, 77.7064), ("Bareilly", 28.3670, 79.4304),
    ], monsoon_boost=0.50, seasonal_phase=0.9, tags=("focus",)),

    StateGeo("KA", "Karnataka", 15.32, 75.71, 330, [
        ("Bengaluru Rural", 13.0827, 77.5877), ("Mysuru", 12.2958, 76.6394),
        ("Belagavi", 15.8497, 74.4977), ("Kalaburagi", 17.3297, 76.8343),
        ("Mangaluru", 12.9141, 74.8560), ("Ballari", 15.1394, 76.9214),
        ("Shivamogga", 13.9299, 75.5681),
    ], monsoon_boost=0.60, seasonal_phase=1.1),

    StateGeo("RJ", "Rajasthan", 27.02, 74.22, 280, [
        ("Jaipur", 26.9124, 75.7873), ("Jodhpur", 26.2389, 73.0243),
        ("Udaipur", 24.5854, 73.7125), ("Kota", 25.2138, 75.8648),
        ("Bikaner", 28.0229, 73.3119), ("Ajmer", 26.4499, 74.6399),
        ("Alwar", 27.5530, 76.6346), ("Barmer", 25.7521, 71.3967),
    ], monsoon_months=(7, 8), monsoon_boost=0.20, seasonal_phase=4.1, spread=0.6),

    StateGeo("MH", "Maharashtra", 19.75, 75.71, 250, [
        ("Pune", 18.5204, 73.8567), ("Nashik", 19.9975, 73.7898),
        ("Nagpur", 21.1458, 79.0882), ("Chhatrapati Sambhajinagar", 19.8762, 75.3433),
        ("Solapur", 17.6599, 75.9064), ("Kolhapur", 16.7050, 74.2433),
        ("Amravati", 20.9320, 77.7523), ("Nanded", 19.1383, 77.3210),
    ], monsoon_boost=0.55, seasonal_phase=0.6, tags=("focus",)),

    StateGeo("BR", "Bihar", 25.10, 85.31, 245, [
        ("Patna", 25.5941, 85.1376), ("Gaya", 24.7914, 85.0002),
        ("Muzaffarpur", 26.1209, 85.3647), ("Bhagalpur", 25.2425, 86.9842),
        ("Darbhanga", 26.1542, 85.8918), ("Purnia", 25.7771, 87.4753),
    ], monsoon_boost=0.65, seasonal_phase=1.0, tags=("focus",)),

    StateGeo("TN", "Tamil Nadu", 11.13, 78.66, 184, [
        ("Coimbatore", 11.0168, 76.9558), ("Madurai", 9.9252, 78.1198),
        ("Tiruchirappalli", 10.7905, 78.7047), ("Salem", 11.6643, 78.1460),
        ("Tirunelveli", 8.7139, 77.7567), ("Vellore", 12.9165, 79.1325),
        ("Thanjavur", 10.7870, 79.1378),
    ], monsoon_months=(10, 11, 12), monsoon_boost=0.55, seasonal_phase=3.2),

    StateGeo("GJ", "Gujarat", 22.26, 71.19, 190, [
        ("Ahmedabad", 23.0225, 72.5714), ("Surat", 21.1702, 72.8311),
        ("Rajkot", 22.3039, 70.8022), ("Vadodara", 22.3072, 73.1812),
        ("Bhavnagar", 21.7645, 72.1519), ("Junagadh", 21.5222, 70.4579),
    ], monsoon_boost=0.35, seasonal_phase=0.4),

    StateGeo("WB", "West Bengal", 22.99, 87.85, 178, [
        ("Kolkata", 22.5726, 88.3639), ("Asansol", 23.6839, 86.9753),
        ("Siliguri", 26.7271, 88.3953), ("Malda", 25.0108, 88.1411),
        ("Murshidabad", 24.1751, 88.2800), ("Midnapore", 22.4257, 87.3199),
    ], monsoon_boost=0.70, seasonal_phase=1.3),

    StateGeo("OD", "Odisha", 20.95, 85.10, 167, [
        ("Bhubaneswar", 20.2961, 85.8245), ("Cuttack", 20.4625, 85.8830),
        ("Sambalpur", 21.4669, 83.9812), ("Berhampur", 19.3150, 84.7941),
        ("Rourkela", 22.2604, 84.8536), ("Balasore", 21.4934, 86.9335),
    ], monsoon_boost=0.68, seasonal_phase=1.2),

    StateGeo("MP", "Madhya Pradesh", 23.47, 77.95, 157, [
        ("Bhopal", 23.2599, 77.4126), ("Indore", 22.7196, 75.8577),
        ("Jabalpur", 23.1815, 79.9864), ("Gwalior", 26.2183, 78.1828),
        ("Ujjain", 23.1765, 75.7885), ("Rewa", 24.5362, 81.2961),
    ], monsoon_boost=0.45, seasonal_phase=0.8),

    StateGeo("AP", "Andhra Pradesh", 15.91, 79.74, 148, [
        ("Visakhapatnam", 17.6868, 83.2185), ("Vijayawada", 16.5062, 80.6480),
        ("Guntur", 16.3067, 80.4365), ("Tirupati", 13.6288, 79.4192),
        ("Kurnool", 15.8281, 78.0373), ("Rajahmundry", 17.0005, 81.8040),
    ], monsoon_boost=0.50, seasonal_phase=1.5),

    StateGeo("AS", "Assam", 26.20, 92.94, 131, [
        ("Guwahati", 26.1445, 91.7362), ("Dibrugarh", 27.4728, 94.9120),
        ("Silchar", 24.8333, 92.7789), ("Jorhat", 26.7509, 94.2037),
        ("Tezpur", 26.6528, 92.7926), ("Nagaon", 26.3464, 92.6840),
    ], monsoon_months=(5, 6, 7, 8, 9), monsoon_boost=0.85, seasonal_phase=1.6),

    StateGeo("TG", "Telangana", 18.11, 79.02, 115, [
        ("Hyderabad", 17.3850, 78.4867), ("Warangal", 17.9689, 79.5941),
        ("Nizamabad", 18.6725, 78.0941), ("Karimnagar", 18.4386, 79.1288),
        ("Khammam", 17.2473, 80.1514),
    ], monsoon_boost=0.48, seasonal_phase=1.0),

    StateGeo("KL", "Kerala", 10.85, 76.27, 110, [
        ("Thiruvananthapuram", 8.5241, 76.9366), ("Kochi", 9.9312, 76.2673),
        ("Kozhikode", 11.2588, 75.7804), ("Thrissur", 10.5276, 76.2144),
        ("Kollam", 8.8932, 76.6141), ("Kannur", 11.8745, 75.3704),
        ("Palakkad", 10.7867, 76.6548),
    ], monsoon_months=(6, 7, 8, 9, 10, 11), monsoon_boost=0.75,
       seasonal_phase=1.4, spread=0.25, tags=("focus",)),

    StateGeo("CT", "Chhattisgarh", 21.28, 81.87, 105, [
        ("Raipur", 21.2514, 81.6296), ("Bilaspur", 22.0797, 82.1409),
        ("Durg", 21.1904, 81.2849), ("Jagdalpur", 19.0785, 82.0147),
        ("Ambikapur", 23.1206, 83.1959),
    ], monsoon_boost=0.60, seasonal_phase=1.0),

    StateGeo("JK", "Jammu & Kashmir", 33.78, 76.58, 91, [
        ("Srinagar", 34.0837, 74.7973), ("Jammu", 32.7266, 74.8570),
        ("Anantnag", 33.7311, 75.1487), ("Baramulla", 34.2090, 74.3428),
    ], monsoon_months=(7, 8), monsoon_boost=0.25, seasonal_phase=5.0, zoom=8,
       tags=("ut",)),

    StateGeo("HP", "Himachal Pradesh", 31.10, 77.17, 76, [
        ("Shimla", 31.1048, 77.1734), ("Kangra", 32.0998, 76.2691),
        ("Mandi", 31.7080, 76.9318), ("Solan", 30.9045, 77.0967),
    ], monsoon_boost=0.40, seasonal_phase=4.6, zoom=8),

    StateGeo("HR", "Haryana", 29.06, 76.09, 70, [
        ("Gurugram", 28.4595, 77.0266), ("Hisar", 29.1492, 75.7217),
        ("Rohtak", 28.8955, 76.6066), ("Karnal", 29.6857, 76.9905),
        ("Ambala", 30.3752, 76.7821),
    ], monsoon_boost=0.30, seasonal_phase=0.7, zoom=8),

    StateGeo("PB", "Punjab", 31.15, 75.34, 67, [
        ("Ludhiana", 30.9010, 75.8573), ("Amritsar", 31.6340, 74.8723),
        ("Jalandhar", 31.3260, 75.5762), ("Patiala", 30.3398, 76.3869),
        ("Bathinda", 30.2110, 74.9455),
    ], monsoon_boost=0.30, seasonal_phase=0.7, zoom=8),

    StateGeo("JH", "Jharkhand", 23.61, 85.28, 38, [
        ("Ranchi", 23.3441, 85.3096), ("Jamshedpur", 22.8046, 86.2029),
        ("Dhanbad", 23.7957, 86.4304), ("Hazaribagh", 23.9925, 85.3637),
    ], monsoon_boost=0.62, seasonal_phase=1.1, zoom=8),

    StateGeo("UK", "Uttarakhand", 30.07, 79.09, 36, [
        ("Dehradun", 30.3165, 78.0322), ("Haridwar", 29.9457, 78.1642),
        ("Nainital", 29.3803, 79.4636), ("Almora", 29.5892, 79.6467),
    ], monsoon_boost=0.55, seasonal_phase=0.9, zoom=8),

    StateGeo("DL", "Delhi", 28.65, 77.15, 25, [
        ("North Delhi", 28.7041, 77.1025), ("South Delhi", 28.5355, 77.2100),
        ("West Delhi", 28.6663, 77.0724),
    ], monsoon_boost=0.35, seasonal_phase=0.8, spread=0.08, zoom=10, tags=("ut",)),

    StateGeo("TR", "Tripura", 23.94, 91.99, 20, [
        ("Agartala", 23.8315, 91.2868), ("Udaipur (TR)", 23.5333, 91.4833),
        ("Dharmanagar", 24.3667, 92.1667),
    ], monsoon_months=(5, 6, 7, 8, 9), monsoon_boost=0.80, seasonal_phase=1.6,
       spread=0.2, zoom=9),

    StateGeo("MN", "Manipur", 24.66, 93.91, 18, [
        ("Imphal", 24.8170, 93.9368), ("Churachandpur", 24.3333, 93.6833),
        ("Thoubal", 24.6422, 94.0100),
    ], monsoon_months=(5, 6, 7, 8, 9), monsoon_boost=0.78, seasonal_phase=1.6,
       spread=0.2, zoom=9),

    StateGeo("AR", "Arunachal Pradesh", 28.22, 94.73, 15, [
        ("Itanagar", 27.0844, 93.6053), ("Pasighat", 28.0667, 95.3333),
        ("Tawang", 27.5860, 91.8594),
    ], monsoon_months=(5, 6, 7, 8, 9), monsoon_boost=0.85, seasonal_phase=1.7,
       spread=0.5, zoom=8),

    StateGeo("ML", "Meghalaya", 25.47, 91.37, 15, [
        ("Shillong", 25.5788, 91.8933), ("Tura", 25.5140, 90.2026),
        ("Jowai", 25.4500, 92.2000),
    ], monsoon_months=(5, 6, 7, 8, 9), monsoon_boost=0.90, seasonal_phase=1.6,
       spread=0.25, zoom=9),

    StateGeo("NL", "Nagaland", 26.16, 94.56, 13, [
        ("Kohima", 25.6751, 94.1086), ("Dimapur", 25.9063, 93.7276),
        ("Mokokchung", 26.3220, 94.5220),
    ], monsoon_months=(5, 6, 7, 8, 9), monsoon_boost=0.78, seasonal_phase=1.6,
       spread=0.2, zoom=9),

    StateGeo("MZ", "Mizoram", 23.16, 92.94, 10, [
        ("Aizawl", 23.7271, 92.7176), ("Lunglei", 22.8800, 92.7300),
    ], monsoon_months=(5, 6, 7, 8, 9), monsoon_boost=0.80, seasonal_phase=1.6,
       spread=0.2, zoom=9),

    StateGeo("GA", "Goa", 15.30, 74.12, 8, [
        ("North Goa", 15.5500, 73.8000), ("South Goa", 15.2000, 74.0000),
    ], monsoon_boost=0.80, seasonal_phase=1.2, spread=0.12, zoom=10),

    StateGeo("SK", "Sikkim", 27.53, 88.51, 6, [
        ("Gangtok", 27.3314, 88.6138), ("Namchi", 27.1667, 88.3500),
    ], monsoon_boost=0.75, seasonal_phase=1.5, spread=0.15, zoom=10),

    StateGeo("LA", "Ladakh", 34.20, 77.60, 5, [
        ("Leh", 34.1526, 77.5771), ("Kargil", 34.5539, 76.1349),
    ], monsoon_months=(), monsoon_boost=0.05, seasonal_phase=5.2,
       spread=0.5, zoom=8, tags=("ut",)),

    StateGeo("PY", "Puducherry", 11.94, 79.81, 5, [
        ("Puducherry", 11.9416, 79.8083),
    ], monsoon_months=(10, 11, 12), monsoon_boost=0.5, seasonal_phase=3.2,
       spread=0.08, zoom=11, tags=("ut",)),

    StateGeo("AN", "Andaman & Nicobar", 11.67, 92.74, 5, [
        ("Port Blair", 11.6234, 92.7265),
    ], monsoon_months=(5, 6, 7, 8, 9), monsoon_boost=0.85, seasonal_phase=1.8,
       spread=0.2, zoom=9, tags=("ut",)),

    StateGeo("CH", "Chandigarh", 30.73, 76.78, 3, [
        ("Chandigarh", 30.7333, 76.7794),
    ], monsoon_boost=0.30, seasonal_phase=0.7, spread=0.05, zoom=11, tags=("ut",)),

    # An archipelago: the jitter is kept tiny on purpose, because a facility
    # scattered a few kilometres off an anchor here lands in the sea.
    StateGeo("LD", "Lakshadweep", 10.57, 72.64, 4, [
        ("Kavaratti", 10.5669, 72.6420), ("Andrott", 10.8167, 73.6833),
    ], monsoon_months=(5, 6, 7, 8, 9), monsoon_boost=0.80, seasonal_phase=1.8,
       spread=0.03, zoom=9, tags=("ut",)),

    # One union territory since the 2020 merger, but still three separate
    # pockets of coast, which is why all three are anchors.
    StateGeo("DH", "Dadra & Nagar Haveli and Daman & Diu", 20.40, 72.83, 10, [
        ("Silvassa", 20.2700, 73.0100), ("Daman", 20.3974, 72.8328),
        ("Diu", 20.7144, 70.9874),
    ], monsoon_boost=0.60, seasonal_phase=0.7, spread=0.08, zoom=9, tags=("ut",)),
]

STATE_BY_CODE: dict[str, StateGeo] = {s.code: s for s in INDIA_STATES}

TOTAL_SEEDED_FACILITIES = sum(s.facilities for s in INDIA_STATES)

# India is 28 states and 8 union territories. The list above holds both, which
# is why it is not named after either; these two make the split checkable.
UNION_TERRITORY_CODES: frozenset[str] = frozenset(
    s.code for s in INDIA_STATES if "ut" in s.tags
)
STATE_CODES: frozenset[str] = frozenset(
    s.code for s in INDIA_STATES if "ut" not in s.tags
)
